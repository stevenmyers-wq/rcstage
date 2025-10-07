import os
from webapp import create_app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    # Note: Set debug=False for production environments
    app.run(host="0.0.0.0", port=port, debug=True)