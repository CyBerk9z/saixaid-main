import azure.functions as func
import logging
import httpx

logger = logging.getLogger(__name__)

async def main(timer: func.TimerRequest) -> None:
    """毎日実行されるメッセージ収集関数"""
    try:
        logger.info("メッセージ収集ジョブを開始します")

        # APIのベースURL
        base_url = "https://inthub-fjfjehdsc4akamdg.japaneast-01.azurewebsites.net/api/v1"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Slackワークスペース一覧を取得
            logger.info("Slackワークスペース一覧を取得中...")
            workspaces_response = await client.get(f"{base_url}/company/list/slack-workspaces")
            workspaces_response.raise_for_status()
            workspaces = workspaces_response.json()
            
            if workspaces["status"] != "success":
                raise Exception("ワークスペース一覧の取得に失敗しました")
            
            logger.info(f"取得したワークスペース数: {len(workspaces['workspaces'])}")
            
            # 2. 各ワークスペースに対してメッセージ取得を実行
            total_messages = 0
            processed_workspaces = 0
            failed_workspaces = []

            for workspace in workspaces["workspaces"]:
                try:
                    company_id = workspace["company_id"]
                    team_id = workspace["team_id"]
                    
                    logger.info(f"ワークスペースの処理を開始: company_id={company_id}, team_id={team_id}")
                    
                    # メッセージ取得APIを呼び出し
                    fetch_response = await client.post(
                        f"{base_url}/slack/fetch",
                        json={
                            "companyId": company_id,
                            "teamId": team_id,
                            "days": 1
                        }
                    )
                    fetch_response.raise_for_status()
                    result = fetch_response.json()
                    
                    if result.get("status") == "success":
                        total_messages += result.get("recordsTotal", 0)
                        processed_workspaces += 1
                        logger.info(
                            f"ワークスペースの処理が完了: company_id={company_id}, "
                            f"取得メッセージ数={result.get('recordsTotal', 0)}"
                        )
                    else:
                        raise Exception(result.get("message", "Unknown error"))

                except Exception as e:
                    error_msg = f"ワークスペースの処理中にエラーが発生: company_id={company_id}, error={str(e)}"
                    logger.error(error_msg, exc_info=True)
                    failed_workspaces.append((company_id, str(e)))
                    continue

            # 処理結果のログ出力
            logger.info("メッセージ収集ジョブが完了")
            logger.info(f"合計取得件数: {total_messages}件")
            logger.info(f"処理完了ワークスペース数: {processed_workspaces}")
            logger.info(f"失敗したワークスペース数: {len(failed_workspaces)}")
            
            if failed_workspaces:
                for company_id, error in failed_workspaces:
                    logger.error(f"失敗したワークスペース {company_id}: {error}")

    except Exception as e:
        error_msg = f"予期せぬエラーが発生しました: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise  # エラーを上位に伝播させて、Azure Functionsのエラーハンドリングを利用 