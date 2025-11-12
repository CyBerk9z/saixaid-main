# 1. ベースイメージの選択 (GLIBCの問題を解決するため、新しいOSバージョンを選択)
FROM python:3.11-slim-bookworm

# 必要なシステムパッケージとRustのビルドツールをインストール
# これにより、cryptographyライブラリが環境内で正しくビルドされる
RUN apt-get update && apt-get install -y \
    build-essential \
    pkg-config \
    libssl-dev \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl https://sh.rustup.rs -sSf | sh -s -- -y

# PATHにCargo（Rustのパッケージマネージャ）を追加
ENV PATH="/root/.cargo/bin:${PATH}"

# 作業ディレクトリの設定
WORKDIR /app

# 2. 依存関係のインストール
# まずrequirements.txtだけをコピーしてインストールすることで、
# コードの変更時に毎回ライブラリを再インストールするのを防ぎ、ビルドを高速化する
COPY ./requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# 3. アプリケーションコードのコピー
COPY . /app

# 4. Prisma Clientの生成
RUN python -m prisma generate --schema=app/db/master_prisma/schema.prisma
RUN python -m prisma generate --schema=app/db/tenant_prisma/schema.prisma

# 5. アプリケーションの起動コマンド
# Gunicornを使ってFastAPIアプリケーションを起動
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--worker-class", "uvicorn.workers.UvicornWorker", "app.main:app"]
