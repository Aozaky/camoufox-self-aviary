"""Official-SDK stdio bridge; bearer stays inside the project container."""
import asyncio
import logging
from pathlib import Path
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

async def main():
    logging.getLogger('httpx').setLevel(logging.WARNING)
    token = Path('/home/kasm-user/camoufox-profile/service/api.token').read_text().strip()
    async with streamablehttp_client('http://127.0.0.1:19280/mcp',
            headers={'Authorization':'Bearer '+token}) as (rx, tx, _):
        async with ClientSession(rx, tx) as remote:
            await remote.initialize()
            server = Server('camoufox-stdio')
            @server.list_tools()
            async def list_tools():
                return (await remote.list_tools()).tools
            @server.call_tool(validate_input=False)
            async def call_tool(name, arguments):
                return await remote.call_tool(name, arguments)
            async with stdio_server() as (stdin, stdout):
                await server.run(stdin, stdout, server.create_initialization_options())

if __name__ == '__main__':
    asyncio.run(main())
