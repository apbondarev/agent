import os
import json
import asyncio
from openai import AsyncOpenAI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

# ==========================================
# 1. КОНФИГУРАЦИЯ LLM
# ==========================================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "ВАШ_API_КЛЮЧ")
OPENAI_API_URL = os.getenv("OPENAI_API_URL", "ВАШ_API_URL")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "ВАШ_MODEL")

# Используем асинхронный клиент
client = AsyncOpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_API_URL,
)

# ==========================================
# 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def mcp_tools_to_openai(mcp_tools):
    """
    Конвертирует инструменты в формате MCP в формат JSON Schema, 
    который понимает OpenAI API.
    """
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
    print("Инициализация MCP сервера...")
    
    # Настраиваем параметры запуска локального MCP-сервера.
    # Здесь мы используем официальный сервер файловой системы, разрешая ему работать 
    # только в текущей директории (os.getcwd()), чтобы агент не полез в системные файлы.
    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", os.getcwd()],
        env=None
    )

    # Открываем канал связи (stdio) с сервером
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Инициализация сессии по стандарту MCP
            await session.initialize()
            print("✅ Успешно подключились к MCP серверу!\n")

            # Динамически запрашиваем у сервера список того, что он умеет
            mcp_tools_response = await session.list_tools()
            tools = mcp_tools_to_openai(mcp_tools_response.tools)
            
            print("Доступные инструменты на сервере:")
            for t in tools:
                print(f" - {t['function']['name']}")

            print(f"\n🤖 {OPENAI_MODEL} запущен! Введите 'exit' для выхода.")

            messages = [
                {"role": "system", "content": "Ты AI-помощник разработчика. Используй предоставленные инструменты (MCP) для решения задач. Всегда думай по шагам."}
            ]

            # Создаем сессию для умного ввода
            prompt_session = PromptSession()

            while True:
                # В асинхронном коде input() блокирует цикл событий, 
                # для продакшена лучше использовать aioconsole, но для прототипа 
                # можно запустить стандартный input в отдельном потоке
                with patch_stdout():
                    user_input = await prompt_session.prompt_async("\nВы: ")
                if user_input.lower() in ['exit', 'quit']:
                    print("Завершение работы.")
                    break
                    
                messages.append({"role": "user", "content": user_input})
                
                # Внутренний цикл общения LLM и Инструментов
                while True:
                    try:
                        response = await client.chat.completions.create(
                            model=OPENAI_MODEL,
                            messages=messages,
                            tools=tools if tools else None
                        )
                        
                        message = response.choices[0].message
                        messages.append(message)
                        
                        # Если LLM решила использовать инструмент
                        if message.tool_calls:
                            for tool_call in message.tool_calls:
                                func_name = tool_call.function.name
                                
                                try:
                                    args = json.loads(tool_call.function.arguments)
                                except json.JSONDecodeError:
                                    args = {}
                                
                                # Защита Human-in-the-loop
                                print(f"\n[⚠️ Агент вызывает MCP-инструмент: {func_name}]")
                                print(f"Параметры: {json.dumps(args, indent=2, ensure_ascii=False)}")
                                with patch_stdout():
                                    confirm = await prompt_session.prompt_async("Разрешить выполнение на сервере? (y/n): ")
                                
                                if confirm.lower() not in ['y', 'yes', '']:
                                    result_text = "System response: User denied permission to execute this tool."
                                else:
                                    try:
                                        # Отправляем команду на выполнение реальному MCP-серверу!
                                        mcp_result = await session.call_tool(func_name, arguments=args)
                                        
                                        # MCP возвращает массив блоков контента. Собираем их в одну строку.
                                        result_text = ""
                                        for content_block in mcp_result.content:
                                            if content_block.type == 'text':
                                                result_text += content_block.text + "\n"
                                        
                                        if not result_text:
                                            result_text = "Tool executed successfully, but returned no text."
                                            
                                    except Exception as e:
                                        result_text = f"Error executing MCP tool: {e}"
                                        print(f"[Ошибка MCP]: {e}")
                                    
                                # Отправляем результат обратно в LLM
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": tool_call.id,
                                    "content": result_text
                                })
                        else:
                            # LLM дала текстовый ответ
                            print(f"\nАгент: {message.content}")
                            break 
                            
                    except Exception as e:
                        print(f"\n[Ошибка API]: {e}")
                        break

if __name__ == "__main__":
    # Запускаем асинхронный цикл
    asyncio.run(main())