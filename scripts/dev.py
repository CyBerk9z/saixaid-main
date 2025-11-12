import uvicorn
import os
import sys

# プロジェクトルートのパスを追加
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,  # ファイルの変更を検知して自動リロード
        log_level="debug"
    )