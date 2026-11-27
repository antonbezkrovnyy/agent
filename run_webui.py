import threading
import webbrowser

import uvicorn


if __name__ == "__main__":
    url = "http://127.0.0.1:8765"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run("webui.server.main:app", host="127.0.0.1", port=8765, reload=False)
