import os
import json
import asyncio
from contextlib import AsyncExitStack
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout


# ==========================================
# 1. КОНФИГУРАЦИЯ LLM
# ==========================================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "ВАШ_API_КЛЮЧ")
OPENAI_API_URL = os.getenv("OPENAI_API_URL", "ВАШ_API_URL")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "ВАШ_MODEL")

client = AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_API_URL,
)

# ==========================================
# 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def mcp_tools_to_openai(mcp_tools):
    openai_tools = []
    for tool in mcp_tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema
            }
        })
    return openai_tools

# ==========================================
# 3. ГЛАВНЫЙ АСИНХРОННЫЙ ЦИКЛ
# ==========================================
async def main():
    # Загружаем конфигурацию серверов
    config_path = "mcp_config.json"
    if not os.path.exists(config_path):
        print(f"❌ Ошибка: Файл конфигурации {config_path} не найден!")
        return

    # 1. Загружаем секреты из безопасного места (домашней директории)
    # Если файла там нет, скрипт просто возьмет переменные вашей ОС
    env_path = os.path.expanduser("~/.agent_env")
    load_dotenv(env_path)

    with open(config_path, "r", encoding="utf-8") as f:
        raw_config = f.read()

    # 2. Магия подстановки: заменяем все ${VAR_NAME} в тексте на реальные значения 
    # из переменных окружения
    expanded_config = os.path.expandvars(raw_config)

    # 3. Теперь парсим готовый JSON со вставленными секретами
    try:
        config = json.loads(expanded_config)
    except json.JSONDecodeError as e:
        print(f"❌ Ошибка парсинга mcp_config.json (возможно, после подстановки сломался синтаксис): {e}")
        return

    # Словари для хранения сессий и маршрутизации инструментов
    sessions = {}
    tool_to_server = {}
    all_openai_tools = []

    # AsyncExitStack позволяет динамически открывать множество асинхронных контекстов
    async with AsyncExitStack() as stack:
        print("🚀 Инициализация MCP серверов...")
        
        mcp_servers = config.get("mcpServers", {})
        for server_name, server_config in mcp_servers.items():
            print(f"🔄 Подключение к серверу: {server_name}...")
            
            # Копируем системные переменные окружения (чтобы работал PATH для npx)
            # и добавляем переменные из конфига (например, API ключи)
            env = os.environ.copy()
            if "env" in server_config:
                env.update(server_config["env"])
            
            server_params = StdioServerParameters(
                command=server_config["command"],
                args=server_config.get("args", []),
                env=env
            )

            try:
                # Динамически входим в контекст stdio и ClientSession
                read, write = await stack.enter_async_context(stdio_client(server_params))
                session = await stack.enter_async_context(ClientSession(read, write))
                
                await session.initialize()
                sessions[server_name] = session
                
                # Запрашиваем инструменты у текущего сервера
                mcp_tools_response = await session.list_tools()
                
                for tool in mcp_tools_response.tools:
                    # Запоминаем, какому серверу принадлежит этот инструмент
                    tool_to_server[tool.name] = server_name
                    print(f"  └─ Загружен инструмент: {tool.name}")
                
                # Конвертируем в формат OpenAI и добавляем в общий пул
                openai_tools = mcp_tools_to_openai(mcp_tools_response.tools)
                all_openai_tools.extend(openai_tools)
                
            except Exception as e:
                print(f"❌ Ошибка подключения к {server_name}: {e}")

        if not all_openai_tools:
            print("⚠️ Не загружено ни одного инструмента. Агент не сможет ничего выполнять.")
        else:
            print(f"\n✅ Успешно! Всего инструментов доступно: {len(all_openai_tools)}")

        print(f"\n🤖 {OPENAI_MODEL} запущен! Введите 'exit' для выхода.")

        messages = [
            {"role": "system", "content": "Ты AI-помощник разработчика. Используй предоставленные инструменты (MCP) для решения задач. Всегда думай по шагам."}
        ]
        
        prompt_session = PromptSession()

        while True:
            with patch_stdout():
                user_input = await prompt_session.prompt_async("\nВы: ")
                
            if user_input.lower() in ['exit', 'quit']:
                print("Завершение работы.")
                break
                
            messages.append({"role": "user", "content": user_input})
            
            while True:
                try:
                    response = await client.chat.completions.create(
                        model=OPENAI_MODEL,
                        messages=messages,
                        tools=all_openai_tools if all_openai_tools else None
                    )
                    
                    message = response.choices[0].message
                    messages.append(message)
                    
                    if message.tool_calls:
                        for tool_call in message.tool_calls:
                            func_name = tool_call.function.name
                            
                            try:
                                args = json.loads(tool_call.function.arguments)
                            except json.JSONDecodeError:
                                args = {}
                            
                            # === РОУТИНГ MCP ===
                            # Ищем, какому серверу принадлежит вызванная функция
                            target_server = tool_to_server.get(func_name)
                            
                            if not target_server or target_server not in sessions:
                                result_text = f"Error: MCP Server for tool '{func_name}' not found."
                                print(f"\n[Ошибка]: {result_text}")
                            else:
                                target_session = sessions[target_server]
                            
                                print(f"\n[⚠️ Агент вызывает: {func_name} (Сервер: {target_server})]")
                                print(f"Параметры: {json.dumps(args, indent=2, ensure_ascii=False)}")
                                
                                with patch_stdout():
                                    confirm = await prompt_session.prompt_async("Разрешить выполнение? (y/n): ")
                                
                                if confirm.lower() not in ['y', 'yes', '']:
                                    result_text = "System response: User denied permission to execute this tool."
                                else:
                                    try:
                                        # Отправляем команду в правильную сессию
                                        mcp_result = await target_session.call_tool(func_name, arguments=args)
                                        
                                        result_text = ""
                                        for content_block in mcp_result.content:
                                            if content_block.type == 'text':
                                                result_text += content_block.text + "\n"
                                        
                                        if not result_text:
                                            result_text = "Tool executed successfully, but returned no text."
                                                
                                    except Exception as e:
                                        result_text = f"Error executing MCP tool: {e}"
                                        print(f"[Ошибка MCP]: {e}")
                                
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": result_text
                            })
                    else:
                        print(f"\nАгент: {message.content}")
                        break 
                        
                except Exception as e:
                    print(f"\n[Ошибка API]: {e}")
                    break

if __name__ == "__main__":
    asyncio.run(main())