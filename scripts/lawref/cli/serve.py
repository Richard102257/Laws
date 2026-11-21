"""`laws serve`：启动 Web 管理端。"""
import click


@click.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True, type=int)
@click.option("--reload", is_flag=True, help="改代码后自动重启，开发用")
def serve(host: str, port: int, reload: bool):
    """在浏览器里采集、整理、查看法规。默认 http://127.0.0.1:8000/。"""
    import uvicorn

    uvicorn.run("web.app:app", host=host, port=port, reload=reload)
