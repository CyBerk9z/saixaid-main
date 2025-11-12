# app/services/slack/slack_service.py
from __future__ import annotations

import requests
import asyncio
import hmac
import hashlib
import time
import csv
import io
import pandas as pd
import httpx
import re
import uuid
from typing import Dict, List, Optional, Any, Set, AsyncGenerator
from datetime import datetime, timezone, UTC, timedelta
from starlette.datastructures import UploadFile, Headers
from fastapi import HTTPException, Request, status
from uuid import UUID
from azure.storage.blob import BlobServiceClient

from app.core.exceptions import AppException, ErrorCode
from app.core.config import settings
from app.services.company.company_service import CompanyService
from app.core.logging import get_logger
from app.services.rag.rag_service import RagService

from app.db.master_prisma.prisma import Prisma
from app.services.azure.key_vault import KeyVaultClient

logger = get_logger(__name__)

_SLACK_BASE_URL = "https://slack.com/api"
_DEFAULT_LIMIT = 200  # Slack が許容する最大値

class SlackService:
    # クラス変数
    _DUP_CACHE: Set[str] = set()
    _DUP_TTL_SEC = 300  # 5 min
    _DUP_HISTORY: List[tuple[float, str]] = []
    _MEMBER_CACHE: Dict[str, tuple[float, List[Dict[str, str]]]] = {}
    _CACHE_TTL = 600  # 10 分

    @staticmethod
    async def _slack_get(endpoint: str, params: Dict, team_id: str) -> Dict:
        """Slack Web‑API の GET をラップし、レートリミットにも対応する。"""

        url = f"{_SLACK_BASE_URL}/{endpoint}"
        token = await KeyVaultClient.get_secret(name=f"slack-token-{team_id}")
        if not token:
            raise AppException(
                ErrorCode.SERVICE_UNAVAILABLE,
                message="Slackトークンの取得に失敗しました"
            )
        headers = {"Authorization": f"Bearer {token}"}

        while True:
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=10)
            except requests.exceptions.RequestException as exc:
                raise AppException(
                    ErrorCode.SERVICE_UNAVAILABLE,
                    message="Slack API に接続できませんでした",
                    context={"endpoint": endpoint, **params},
                ) from exc

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "1"))
                logger.warning(f"Slack 429 rate‑limit – {retry_after} 秒待機 …")
                time.sleep(retry_after)
                continue

            try:
                resp.raise_for_status()
            except requests.exceptions.HTTPError as exc:
                raise AppException(
                    ErrorCode.SERVICE_UNAVAILABLE,
                    message=f"Slack から HTTP {resp.status_code} が返されました",
                    context={"endpoint": endpoint, **params},
                ) from exc

            data = resp.json()
            if not data.get("ok"):
                raise AppException(
                    ErrorCode.SERVICE_UNAVAILABLE,
                    message=f"Slack API エラー: {data.get('error', '不明なエラー')}",
                    context={"endpoint": endpoint, **params},
                )
            return data

    @staticmethod
    async def fetch_and_upload_messages(
        company_id: str,
        team_id: str,
        channel_ids: List[str],
        from_dt: datetime,
        to_dt: datetime,
        include_threads: bool,
        job_id: str,
    ) -> Dict:
        """上位レベルのオーケストレーション：メッセージを取得し、CSV として Blob に保存し、インデックス化する。"""
        
        oldest_ts = from_dt.replace(tzinfo=timezone.utc).timestamp()
        latest_ts = to_dt.replace(tzinfo=timezone.utc).timestamp()

        # ストリーミング用の一時ファイルを作成
        temp_csv_path = f"/tmp/slack_export_{job_id}.csv"
        total_records = 0
        chunk_size = 1000  # メモリに保持するメッセージの最大数

        try:
            # CSVヘッダーを書き込み
            with open(temp_csv_path, 'w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'Timestamp', 'User ID', 'User Name', 'Channel', 
                    'Message', 'Attachments', 'Parent Message Timestamp'
                ])
                writer.writeheader()

            # チャンネルごとに処理
            for ch_id in channel_ids:
                current_chunk = []
                async for msg in SlackService._stream_channel_history(
                    channel_id=ch_id,
                    oldest=oldest_ts,
                    latest=latest_ts,
                    include_threads=include_threads,
                    team_id=team_id,
                ):
                    # メッセージを整形
                    ts = msg.get('ts')
                    if not ts:
                        continue

                    timestamp = datetime.fromtimestamp(float(ts)).strftime('%Y-%m-%d %H:%M:%S')
                    attachments = '; '.join([file.get('name', 'N/A') for file in msg.get('files', [])])
                    thread_ts = msg.get('thread_ts', '')
                    parent_timestamp = (
                        datetime.fromtimestamp(float(thread_ts)).strftime('%Y-%m-%d %H:%M:%S')
                        if thread_ts and thread_ts != ts
                        else ''
                    )
                    user_profile = msg.get('user_profile', {})
                    user_name = user_profile.get('real_name', 'システムメッセージ')

                    row = {
                        'Timestamp': timestamp,
                        'User ID': msg.get('user', 'システムメッセージ'),
                        'User Name': user_name,
                        'Channel': msg.get('channel', ''),
                        'Message': msg.get('text', ''),
                        'Attachments': attachments,
                        'Parent Message Timestamp': parent_timestamp,
                    }

                    current_chunk.append(row)
                    total_records += 1

                    # チャンクサイズに達したらファイルに書き込み
                    if len(current_chunk) >= chunk_size:
                        with open(temp_csv_path, 'a', encoding='utf-8-sig', newline='') as f:
                            writer = csv.DictWriter(f, fieldnames=row.keys())
                            writer.writerows(current_chunk)
                        current_chunk = []

                # 残りのメッセージを書き込み
                if current_chunk:
                    with open(temp_csv_path, 'a', encoding='utf-8-sig', newline='') as f:
                        writer = csv.DictWriter(f, fieldnames=current_chunk[0].keys())
                        writer.writerows(current_chunk)

            # 一時ファイルをUploadFileとして読み込み
            with open(temp_csv_path, 'rb') as f:
                upload_file = UploadFile(
                    filename=f"slack_export_{job_id}.csv",
                    file=io.BytesIO(f.read()),
                    headers=Headers({"content-type": "text/csv"})
                )

            # Azure Blob に保存
            upload_meta = await CompanyService.upload_csv_to_blob(company_id, upload_file)
            file_id = upload_meta["fileId"]

            # インデックス化を実行
            logger.info(f"Starting indexing for company_id: {company_id}")
            try:
                await RagService.build_index(
                    company_id=company_id,
                    file_id=file_id,
                )
                logger.info(f"Indexing completed for company_id: {company_id}")
            except Exception as e:
                logger.error(f"Error during indexing: {str(e)}", exc_info=True)
                raise AppException(
                    ErrorCode.INTERNAL_SERVER_ERROR,
                    message="インデックス化に失敗しました",
                )

            # 最終同期時刻を更新
            try:
                async with Prisma() as db:
                    await db.slackworkspace.update(
                        where={"teamId": team_id},
                        data={"lastSyncAt": datetime.fromtimestamp(latest_ts, UTC)}
                    )
                logger.info(f"Updated last_sync_at to {latest_ts} for team {team_id}")
            except Exception as e:
                logger.error(f"Error updating last_sync_at: {str(e)}", exc_info=True)

            logger.info(
                f"Slack fetch job {job_id} completed – channels={len(channel_ids)} rows={total_records}"
            )

            return {"recordsTotal": total_records, "blobMeta": upload_meta}

        finally:
            # 一時ファイルを削除
            try:
                import os
                if os.path.exists(temp_csv_path):
                    os.remove(temp_csv_path)
            except Exception as e:
                logger.error(f"Error removing temporary file: {str(e)}")

    @staticmethod
    async def _stream_channel_history(
        channel_id: str,
        oldest: float,
        latest: float,
        include_threads: bool,
        team_id: str,
    ) -> AsyncGenerator[Dict[str, str], None]:
        """チャンネル履歴をストリーミング形式で取得するジェネレータ"""
        cursor: Optional[str] = None

        while True:
            params = {
                "channel": channel_id,
                "oldest": oldest,
                "latest": latest,
                "limit": _DEFAULT_LIMIT,
                "inclusive": True,
            }
            if cursor:
                params["cursor"] = cursor

            data = await SlackService._slack_get("conversations.history", params, team_id)

            for msg in data.get("messages", []):
                if msg.get("subtype"):
                    continue

                # ユーザー情報を取得
                if msg.get("user"):
                    try:
                        user_data = await SlackService._slack_get(
                            "users.info",
                            {"user": msg["user"]},
                            team_id
                        )
                        if user_data.get("ok"):
                            msg["user_profile"] = user_data["user"]
                    except Exception as e:
                        logger.error(f"Error fetching user info: {str(e)}")

                yield msg

                # スレッド取得（有効な場合）
                if include_threads and msg.get("reply_count") and msg.get("thread_ts"):
                    async for reply in SlackService._stream_thread_replies(
                        channel_id, msg["thread_ts"], oldest, latest, team_id
                    ):
                        yield reply

            if data.get("has_more"):
                cursor = data.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
            else:
                break

    @staticmethod
    async def _stream_thread_replies(
        channel_id: str,
        thread_ts: str,
        oldest: float,
        latest: float,
        team_id: str,
    ) -> AsyncGenerator[Dict[str, str], None]:
        """スレッドの返信をストリーミング形式で取得するジェネレータ"""
        cursor: Optional[str] = None

        while True:
            params = {
                "channel": channel_id,
                "ts": thread_ts,
                "oldest": oldest,
                "latest": latest,
                "limit": _DEFAULT_LIMIT,
            }
            if cursor:
                params["cursor"] = cursor

            data = await SlackService._slack_get("conversations.replies", params, team_id)

            for msg in data.get("messages", []):
                if msg.get("ts") == thread_ts or msg.get("subtype"):
                    continue

                # ユーザー情報を取得
                if msg.get("user"):
                    try:
                        user_data = await SlackService._slack_get(
                            "users.info",
                            {"user": msg["user"]},
                            team_id
                        )
                        if user_data.get("ok"):
                            msg["user_profile"] = user_data["user"]
                    except Exception as e:
                        logger.error(f"Error fetching user info: {str(e)}")

                yield msg

            if data.get("has_more"):
                cursor = data.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
            else:
                break

    @staticmethod
    def is_duplicate(event_id: str) -> bool:
        """イベントの重複をチェックする"""
        now = time.time()
        # 古い ID を掃除
        while SlackService._DUP_HISTORY and now - SlackService._DUP_HISTORY[0][0] > SlackService._DUP_TTL_SEC:
            _, old_id = SlackService._DUP_HISTORY.pop(0)
            SlackService._DUP_CACHE.discard(old_id)

        if event_id in SlackService._DUP_CACHE:
            return True

        SlackService._DUP_CACHE.add(event_id)
        SlackService._DUP_HISTORY.append((now, event_id))
        return False

    @staticmethod
    def verify_slack_signature(req: Request, body: bytes) -> None:
        """Slackの署名を検証する"""
        ts = req.headers.get("x-slack-request-timestamp")
        slack_sig = req.headers.get("x-slack-signature", "")
        if ts is None:
            raise HTTPException(status_code=400, detail="Missing timestamp")

        # リプレイ攻撃対策（±5 分）
        if abs(time.time() - int(ts)) > 60 * 5:
            raise HTTPException(status_code=400, detail="Stale request")

        basestring = f"v0:{ts}:{body.decode()}".encode()
        my_sig = (
            "v0="
            + hmac.new(
                settings.SLACK_SIGNING_SECRET.encode(),
                basestring,
                hashlib.sha256,
            ).hexdigest()
        )
        if not hmac.compare_digest(my_sig, slack_sig):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad signature")

    @staticmethod
    async def get_channel_members(
        client: httpx.AsyncClient,
        channel_id: str,
        team_id: str,
    ) -> List[Dict[str, str]]:
        """チャンネルのメンバーを取得する"""
        try:
            now = asyncio.get_event_loop().time()
            if channel_id in SlackService._MEMBER_CACHE and now - SlackService._MEMBER_CACHE[channel_id][0] < SlackService._CACHE_TTL:
                logger.info(f"Using cached members for channel {channel_id}")
                return SlackService._MEMBER_CACHE[channel_id][1]

            logger.info(f"Fetching members for channel {channel_id}")
            members: List[str] = []
            cursor: str | None = None

            # トークンを取得
            token = await KeyVaultClient.get_secret(name=f"slack-token-{team_id}")
            if not token:
                raise AppException(
                    ErrorCode.SERVICE_UNAVAILABLE,
                    message="Slackトークンの取得に失敗しました"
                )
            headers = {"Authorization": f"Bearer {token}"}

            while True:
                logger.info(f"Fetching page of members with cursor: {cursor}")
                resp = await client.get(
                    "https://slack.com/api/conversations.members",
                    params={"channel": channel_id, "cursor": cursor},
                    headers=headers,
                )
                data = resp.json()
                if not data.get("ok"):
                    logger.error(f"conversations.members error: {data.get('error')}")
                    break
                members.extend(data["members"])
                logger.info(f"Retrieved {len(data['members'])} members in this page")
                cursor = data.get("response_metadata", {}).get("next_cursor") or None
                if not cursor:
                    break

            logger.info(f"Total members retrieved: {len(members)}")

            # users.info を並列取得
            logger.info("Fetching user details for all members")
            coros = [
                client.get(
                    "https://slack.com/api/users.info",
                    params={"user": uid},
                    headers=headers,
                )
                for uid in members
            ]
            results = await asyncio.gather(*coros, return_exceptions=True)

            options: List[Dict[str, str]] = []
            for res in results:
                if isinstance(res, Exception):
                    logger.error(f"Error fetching user info: {str(res)}")
                    continue
                data = res.json()
                if data.get("ok"):
                    user: Dict[str, Any] = data["user"]
                    if not user.get("is_bot"):
                        name = user.get("real_name") or user.get("name")
                        options.append(
                            {"text": f"{name}先輩", "value": user["id"]}  # value は user_id
                        )
                        logger.info(f"Added user to options: {name} ({user['id']})")

            logger.info(f"Total non-bot users found: {len(options)}")
            SlackService._MEMBER_CACHE[channel_id] = (now, options)
            return options
        except Exception as e:
            logger.error(f"Error in get_channel_members: {str(e)}", exc_info=True)
            raise

    @staticmethod
    async def _send_select_menu(
        client: httpx.AsyncClient,
        channel_id: str,
        user_id: str,
        question: str,
        thread_ts: str | None,
        thread_messages: List[Dict[str, Any]],
        team_id: str,
    ) -> None:
        """セレクトメニューを送信する"""
        try:
            logger.info(f"Getting channel members for channel {channel_id}")
            options = await SlackService.get_channel_members(client, channel_id, team_id)
            logger.info(f"Retrieved {len(options)} channel members")

            if not options:
                logger.error(f"No channel members found for channel {channel_id}")
                raise RuntimeError(f"No channel members found for channel {channel_id}")

            # 質問内容を改行で分割し、各行を引用形式に変換
            formatted_question = "\n".join(f">{line}" for line in question.split("\n"))

            # トークンを取得
            token = await KeyVaultClient.get_secret(name=f"slack-token-{team_id}")
            if not token:
                raise AppException(
                    ErrorCode.SERVICE_UNAVAILABLE,
                    message="Slackトークンの取得に失敗しました"
                )
            headers = {"Authorization": f"Bearer {token}"}

            payload = {
                "channel": channel_id,
                "text": f"Hey <@{user_id}> 先輩を選んでください！",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*質問内容*:\n{formatted_question}\n\nどの先輩に質問しますか？",
                        },
                        "accessory": {
                            "type": "static_select",
                            "action_id": "senpai_select",
                            "placeholder": {"type": "plain_text", "text": "先輩を選ぶ"},
                            "options": [
                                {
                                    "text": {"type": "plain_text", "text": o["text"]},
                                    "value": o["value"],
                                }
                                for o in options
                            ],
                        },
                    }
                ],
                "metadata": {
                    "event_type": "senpai_question",
                    "event_payload": {
                        "question": question,  # 元の質問内容はそのまま保持
                        "thread_ts": thread_ts,
                        "thread_messages": thread_messages,
                        "senpai": None,
                    },
                },
            }
            if thread_ts:
                payload["thread_ts"] = thread_ts

            logger.info(f"Sending message to Slack with payload: {payload}")
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers=headers,
                json=payload,
            )
            resp_data = resp.json()
            if not resp_data.get("ok"):
                logger.error(f"Slack API error: {resp_data}")
                raise RuntimeError(f"Slack error: {resp.text}")
            logger.info("Message sent successfully")
        except Exception as e:
            logger.error(f"Error in send_select_menu: {str(e)}", exc_info=True)
            raise

    @staticmethod
    async def get_thread_messages(
        client: httpx.AsyncClient,
        channel_id: str,
        thread_ts: str,
        team_id: str,
    ) -> List[Dict[str, Any]]:
        """スレッド内のメッセージを取得"""
        try:
            # トークンを取得
            token = await KeyVaultClient.get_secret(name=f"slack-token-{team_id}")
            if not token:
                raise AppException(
                    ErrorCode.SERVICE_UNAVAILABLE,
                    message="Slackトークンの取得に失敗しました"
                )
            headers = {"Authorization": f"Bearer {token}"}

            resp = await client.get(
                "https://slack.com/api/conversations.replies",
                params={"channel": channel_id, "ts": thread_ts},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data.get("ok"):
                logger.error(f"Failed to get thread messages: {data.get('error')}")
                return []

            msgs = sorted(
                data.get("messages", []),
                key=lambda x: float(x.get("ts", 0)),
            )
            return [
                {"user": m["user"], "text": m["text"], "ts": m["ts"]}
                for m in msgs
                if m.get("type") == "message" and not m.get("subtype")
            ]
        except Exception as exc:
            logger.error(f"Error getting thread messages: {exc}")
            return []

    @staticmethod
    async def handle_mention(event: Dict[str, Any], client: httpx.AsyncClient) -> None:
        """メンションを処理する"""
        try:
            channel_id = event["channel"]
            user_id = event["user"]
            thread_ts = event.get("thread_ts")
            team_id = event.get("team")
            logger.info(f"Processing mention in channel {channel_id} from user {user_id}")

            if not team_id:
                logger.error("team_id not found in event")
                return
            logger.info(f"team_id: {team_id}")

            # メッセージ全体を取得し、メンション部分を除去
            text = event.get("text", "")
            # メンション部分（<@U...>）を除去
            question = re.sub(r'<@[A-Z0-9]+>', '', text).strip()
            logger.info(f"Full question: {question}")

            thread_messages: List[Dict[str, Any]] = []
            if thread_ts:
                thread_messages = await SlackService.get_thread_messages(client, channel_id, thread_ts, team_id)
                logger.info(f"Retrieved {len(thread_messages)} thread messages")

            await SlackService._send_select_menu(
                client=client,
                channel_id=channel_id,
                user_id=user_id,
                question=question,
                thread_ts=thread_ts,
                thread_messages=thread_messages,
                team_id=team_id,
            )
            logger.info("Select menu sent successfully")
        except Exception as e:
            logger.error(f"Error in handle_mention: {str(e)}", exc_info=True)

    @staticmethod
    async def join_channel(client: httpx.AsyncClient, channel_id: str, team_id: str) -> bool:
        """チャンネルに参加する"""
        try:
            # トークンを取得
            token = await KeyVaultClient.get_secret(name=f"slack-token-{team_id}")
            if not token:
                raise AppException(
                    ErrorCode.SERVICE_UNAVAILABLE,
                    message="Slackトークンの取得に失敗しました"
                )
            headers = {"Authorization": f"Bearer {token}"}

            response = await client.post(
                "https://slack.com/api/conversations.join",
                params={"channel": channel_id},
                headers=headers
            )
            result = response.json()
            if not result.get("ok"):
                logger.warning(f"Failed to join channel {channel_id}: {result.get('error')}")
                return False
            return True
        except Exception as e:
            logger.error(f"Error joining channel {channel_id}: {e}")
            return False

    @staticmethod
    async def send_slack_notification(
        client: httpx.AsyncClient,
        response_url: str,
        message: str,
        max_retries: int = 3,
        retry_delay: float = 1.0
    ) -> bool:
        """Slack通知を送信する関数（リトライ機能付き）"""
        for attempt in range(max_retries):
            try:
                response = await client.post(
                    response_url,
                    json={"text": message},
                    timeout=10.0  # タイムアウトを設定
                )
                response.raise_for_status()
                return True
            except Exception as e:
                logger.error(
                    f"Failed to send Slack notification (attempt {attempt + 1}/{max_retries}): {str(e)}",
                    exc_info=True
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (2 ** attempt))  # 指数バックオフ
                else:
                    logger.error(f"All retry attempts failed for message: {message[:100]}...")
                    return False
        return False

    @staticmethod
    async def process_messages_async(
        company_id: str,
        team_id: str,
        channel_ids: List[str],
        from_dt: datetime,
        to_dt: datetime,
        include_threads: bool,
        response_url: str | None,
        client: httpx.AsyncClient
    ):
        """非同期でメッセージを処理する関数"""
        try:
            # 取得処理用のジョブ ID を生成
            job_id = f"job_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
            logger.info(f"Starting message processing job {job_id} for {len(channel_ids)} channels")
            
            # 処理開始メッセージを送信
            initial_message = (
                f"メッセージの取得を開始しました。\n"
                f"ジョブID: {job_id}\n"
                f"対象チャンネル数: {len(channel_ids)}\n"
                f"処理が完了するまでお待ちください。"
            )
            if response_url:    
                if not await SlackService.send_slack_notification(client, response_url, initial_message):
                    logger.error("Failed to send initial notification after all retries")

            # チャンネルを小さなグループに分割
            chunk_size = 5  # 一度に処理するチャンネル数を減らす
            channel_chunks = [channel_ids[i:i + chunk_size] for i in range(0, len(channel_ids), chunk_size)]
            logger.info(f"Split {len(channel_ids)} channels into {len(channel_chunks)} chunks (size: {chunk_size})")
            
            total_records = 0
            failed_chunks = []
            chunk_results = []  # 各チャンクの処理結果を記録

            for i, chunk in enumerate(channel_chunks):
                chunk_start_time = datetime.now(UTC)
                chunk_id = f"{job_id}_chunk_{i+1}"
                logger.info(f"Processing chunk {i+1}/{len(channel_chunks)} (ID: {chunk_id}) - Channels: {chunk}")
                
                try:
                    # 各チャンクの処理
                    logger.info(f"Starting fetch_and_upload_messages for chunk {i+1}/{len(channel_chunks)}")
                    fetch_result = await SlackService.fetch_and_upload_messages(
                        company_id=company_id,
                        team_id=team_id,
                        channel_ids=chunk,
                        from_dt=from_dt,
                        to_dt=to_dt,
                        include_threads=include_threads,
                        job_id=chunk_id,
                    )
                    total_records += fetch_result.get("recordsTotal", 0)

                    # 処理時間を計算
                    chunk_duration = (datetime.now(UTC) - chunk_start_time).total_seconds()
                    logger.info(
                        f"Completed chunk {i+1}/{len(channel_chunks)} in {chunk_duration:.1f} seconds. "
                        f"Records: {fetch_result.get('recordsTotal', 0)}"
                    )
                    
                    # チャンクの処理結果を記録
                    chunk_results.append({
                        "chunk_number": i + 1,
                        "records": fetch_result.get("recordsTotal", 0),
                        "duration": chunk_duration,
                        "status": "success"
                    })

                    # チャンク間で少し待機
                    logger.info(f"Waiting 2 seconds before next chunk")
                    await asyncio.sleep(2)

                except Exception as chunk_error:
                    error_msg = f"チャンク {i+1}/{len(channel_chunks)} の処理中にエラーが発生しました: {str(chunk_error)}"
                    logger.error(error_msg, exc_info=True)
                    failed_chunks.append((i+1, str(chunk_error)))
                    
                    # チャンクの処理結果を記録（エラー）
                    chunk_results.append({
                        "chunk_number": i + 1,
                        "records": 0,
                        "duration": (datetime.now(UTC) - chunk_start_time).total_seconds(),
                        "status": "error",
                        "error": str(chunk_error)
                    })

                    # エラー発生時も少し待機
                    logger.info(f"Waiting 2 seconds after error before next chunk")
                    await asyncio.sleep(2)

            # 処理完了メッセージを送信（詳細な結果を含める）
            completion_message = (
                f"メッセージの取得が完了しました。\n"
                f"ジョブID: {job_id}\n"
                f"合計取得件数: {total_records}件\n"
                f"処理チャンク数: {len(channel_chunks)}\n\n"
                f"処理結果の詳細:\n"
            )

            # 成功したチャンクの結果を追加
            successful_chunks = [r for r in chunk_results if r["status"] == "success"]
            if successful_chunks:
                completion_message += f"\n成功したチャンク ({len(successful_chunks)}/{len(channel_chunks)}):\n"
                for result in successful_chunks:
                    completion_message += (
                        f"- チャンク {result['chunk_number']}: "
                        f"{result['records']}件 ({result['duration']:.1f}秒)\n"
                    )

            # 失敗したチャンクの結果を追加
            if failed_chunks:
                completion_message += f"\n失敗したチャンク ({len(failed_chunks)}/{len(channel_chunks)}):\n"
                for chunk_num, error in failed_chunks:
                    completion_message += f"- チャンク {chunk_num}: {error}\n"

            logger.info(f"Job {job_id} completed. Total records: {total_records}, Failed chunks: {len(failed_chunks)}")

            if response_url:
                if not await SlackService.send_slack_notification(client, response_url, completion_message):
                    logger.error("Failed to send completion notification after all retries")

        except Exception as e:
            error_msg = f"メッセージ取得中にエラーが発生しました: {str(e)}"
            logger.error(error_msg, exc_info=True)
            if response_url:
                if not await SlackService.send_slack_notification(client, response_url, error_msg):
                    logger.error("Failed to send error notification after all retries")

    @staticmethod
    async def process_fetch_messages(
        company_id: str,
        team_id: str,
        response_url: str | None,
        client: httpx.AsyncClient,
        days: int
    ):
        """メッセージ取得の非同期処理"""
        try:
            # トークンを取得
            token = await KeyVaultClient.get_secret(name=f"slack-token-{team_id}")
            if not token:
                raise AppException(
                    ErrorCode.SERVICE_UNAVAILABLE,
                    message="Slackトークンの取得に失敗しました"
                )
            headers = {"Authorization": f"Bearer {token}"}

            # オープンチャンネルの一覧を取得
            logger.info("Fetching list of public channels...")
            channels_response = await client.get(
                "https://slack.com/api/conversations.list",
                params={
                    "types": "public_channel",
                    "limit": 1000,
                    "exclude_archived": True
                },
                headers=headers
            )
            
            if not channels_response.json().get("ok"):
                error_msg = f"チャンネル一覧の取得に失敗しました: {channels_response.json().get('error')}"
                logger.error(error_msg)
                if response_url:
                    await client.post(
                        response_url,
                        json={
                        "response_type": "ephemeral",
                        "text": error_msg
                    }
                )
                return
            
            channels = channels_response.json().get("channels", [])
            active_channels = [channel for channel in channels if not channel.get("is_archived", False)]
            channel_ids = [channel["id"] for channel in active_channels]
            
            logger.info(f"Found {len(channel_ids)} active public channels")

            # 各チャンネルに参加
            joined_channels = []
            for ch_id in channel_ids:
                if await SlackService.join_channel(client, ch_id, team_id):
                    joined_channels.append(ch_id)
            
            if not joined_channels:
                error_msg = "チャンネルへの参加に失敗しました"
                logger.error(error_msg)
                if response_url:
                    await client.post(
                        response_url,
                        json={
                        "response_type": "ephemeral",
                        "text": error_msg
                    }
                )
                return

            # メッセージ取得処理を開始
            await SlackService.process_messages_async(
                team_id=team_id,
                company_id=company_id,
                channel_ids=joined_channels,
                from_dt=datetime.now(UTC) - timedelta(days=days),
                to_dt=datetime.now(UTC),
                include_threads=True,
                response_url=response_url,
                client=client
            )

        except Exception as e:
            logger.error(f"Error in process_fetch_messages: {e}", exc_info=True)
            if response_url:
                await client.post(
                    response_url,
                    json={
                    "response_type": "ephemeral",
                    "text": f"メッセージ取得中にエラーが発生しました: {str(e)}"
                }
            )

    @staticmethod
    async def get_workspace_token(team_id: str) -> str:
        """ワークスペースのトークンを取得する"""
        try:
            token = await KeyVaultClient.get_secret(name=f"slack-token-{team_id}")
            if not token:
                raise AppException(
                    ErrorCode.NOT_FOUND,
                    message="Slackトークンが見つかりません",
                )
            return token
        except Exception as e:
            logger.error(f"Failed to get workspace token: {str(e)}", exc_info=True)
            raise AppException(
                ErrorCode.INTERNAL_SERVER_ERROR,
                message="Slackトークンの取得に失敗しました",
            )

    @staticmethod
    async def get_headers(team_id: str) -> Dict[str, str]:
        """ワークスペース固有のヘッダーを取得する"""
        token = await SlackService.get_workspace_token(team_id)
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    async def get_company_id_by_team_id(team_id: str) -> str:
        """Slackのteam_idからcompany_idを取得する"""
        try:
            async with Prisma() as db:
                # ワークスペース情報を取得
                workspace = await db.slackworkspace.find_first(
                    where={"teamId": team_id},
                    include={"tenant": True}
                )
                if not workspace:
                    raise AppException(
                        ErrorCode.NOT_FOUND,
                        message="Slackワークスペースが見つかりません",
                    )
                
                # テナント情報からcompany_idを取得
                if not workspace.tenant:
                    raise AppException(
                        ErrorCode.NOT_FOUND,
                        message="テナント情報が見つかりません",
                    )
                
                logger.info(f"Retrieved company_id: {workspace.tenant.companyId} for team_id: {team_id}")
                return workspace.tenant.companyId

        except AppException:
            raise
        except Exception as e:
            logger.error(f"Failed to get company_id: {str(e)}", exc_info=True)
            raise AppException(
                ErrorCode.INTERNAL_SERVER_ERROR,
                message="company_idの取得に失敗しました",
            )

    @staticmethod
    async def process_rag_and_update(
        client: httpx.AsyncClient,
        channel_id: str,
        message_ts: str,
        senpai: str,
        question: str,
        thread_ts: str,
        thread_messages: List[Dict[str, Any]],
        team_id: str,
    ) -> None:
        """RAG処理を実行し、結果をSlackに投稿します"""
        headers = None
        try:
            logger.info(f"Processing question for senpai: {senpai}")
            
            # トークンを取得
            token = await KeyVaultClient.get_secret(name=f"slack-token-{team_id}")
            if not token:
                raise AppException(
                    ErrorCode.SERVICE_UNAVAILABLE,
                    message="Slackトークンの取得に失敗しました"
                )
            
            # チャンネル情報からチームIDを取得
            channel_info_resp = await client.get(
                "https://slack.com/api/conversations.info",
                params={"channel": channel_id},
                headers={"Authorization": f"Bearer {token}"},
            )
            channel_info = channel_info_resp.json()
            if not channel_info.get("ok"):
                raise AppException(
                    ErrorCode.SERVICE_UNAVAILABLE,
                    message="チャンネル情報の取得に失敗しました",
                )

            # ヘッダーを設定
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }

            # 先輩のユーザー情報を取得
            logger.info(f"Fetching user info for senpai: {senpai}")
            user_resp = await client.get(
                "https://slack.com/api/users.info",
                params={"user": senpai},
                headers=headers,
            )
            user_data = user_resp.json()
            if not user_data.get("ok"):
                logger.error(f"Failed to get user info: {user_data.get('error')}")
                senpai_name = senpai
            else:
                user = user_data["user"]
                senpai_name = user.get("real_name") or user.get("name") or senpai
                logger.info(f"Retrieved senpai name: {senpai_name}")

            # スレッドのコンテキストを構築
            ctx = "\n".join(f"User {m['user']}: {m['text']}" for m in thread_messages)

            # クエリを構築
            query = f"""以下の質問について、{senpai_name}の発言を中心に回答してください。

            スレッドのコンテキスト:
            {ctx}

            質問: {question}"""

            # ワークスペースからcompany_idを取得
            company_id = await SlackService.get_company_id_by_team_id(team_id)
            logger.info(f"Retrieved company_id: {company_id}")

            # RAG処理を実行
            answer = await RagService.query_index(query=query, company_id=company_id, top_k=10)

            # 回答メッセージを構築
            response_text = f"*{senpai_name}* からの回答です:\n{answer['answer']}"
            logger.info(f"Response text: {response_text}")

            # 回答生成中のメッセージを更新
            await client.post(
                "https://slack.com/api/chat.update",
                headers=headers,
                json={
                    "channel": channel_id,
                    "ts": message_ts,
                    "text": response_text,
                    "thread_ts": thread_ts
                }
            )

        except Exception as e:
            logger.error(f"Error in RAG processing: {str(e)}", exc_info=True)
            error_text = "申し訳ありません。回答の生成中にエラーが発生しました。"
            if headers:
                try:
                    await client.post(
                        "https://slack.com/api/chat.update",
                        headers=headers,
                        json={
                            "channel": channel_id,
                            "ts": message_ts,
                            "text": error_text,
                            "thread_ts": thread_ts
                        }
                    )
                except Exception as post_error:
                    logger.error(f"Failed to post error message: {str(post_error)}")
        
        try:
            payload = {
                "channel": channel_id,
                "thread_ts": thread_ts,
                "text": response_text,
                "ts": message_ts,
            }
            logger.info("Updating message with RAG response...")
            await client.post(
                "https://slack.com/api/chat.update",
                headers=HEADERS,
                json=payload,
            )
            logger.info("Message updated successfully")
        except Exception as exc:
            logger.error(f"RAG error: {exc}", exc_info=True)
            logger.info("Sending error message to Slack...")
            await client.post(
                "https://slack.com/api/chat.update",
                headers=HEADERS,
                json={
                    "channel": channel_id,
                    "thread_ts": thread_ts,
                    "ts": message_ts,
                    "text": "申し訳ありません。回答生成中にエラーが発生しました。",
                },
            )
            logger.info("Error message sent successfully")

        
