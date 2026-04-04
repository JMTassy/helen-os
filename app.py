"""HELEN OS - Flask Application Entry Point"""

from helen_os.api_server import create_app

app = create_app()

if __name__ == "__main__":
    app.run()
