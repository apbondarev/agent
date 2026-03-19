import os
import json
import subprocess
from openai import OpenAI

# Этот файл (`agent.py`) представляет собой **полностью рабочий прототип консольного AI-ассистента** (агента), который умеет не просто общаться с вами, но и совершать реальные действия на вашем компьютере. 
# Если вкратце, это ваш личный Junior-разработчик, живущий в терминале, мозгом которого выступает нейросеть.
# Вот подробное описание того, как он устроен и что делает:
#
# ### 1. Подключение к «мозгу» (OpenAI agent)
# Скрипт использует стандартную библиотеку `openai`, но перенаправляет её запросы на серверы где работает модель OpenAI
#
# ### 2. Набор навыков (Инструменты)
# В коде заложен массив `tools`, который объясняет нейросети, какие "руки" у неё есть. Сейчас их две:
# * **`read_file`**: Позволяет агенту заглянуть внутрь любого текстового файла на вашем компьютере.
# * **`run_shell_command`**: Дает агенту доступ к терминалу (командной строке). Благодаря этому он может посмотреть список файлов в папке, запустить тесты, сделать `git status` или найти что-то через `grep`.
#
# ### 3. Механизм безопасности (Human-in-the-loop)
# Скрипт спроектирован так, чтобы агент не мог навредить вашей системе. Когда LLM решает, что ей нужно выполнить команду в терминале (например, `rm -rf /` или даже просто `ls`), скрипт ставит процесс на паузу и **спрашивает ваше разрешение** («Разрешить? (y/n)»). Вы можете согласиться, отказаться или даже написать свою версию команды, которую скрипт выполнит вместо предложенной.
#
# ### 4. Бесконечный цикл мышления (Agent Loop)
# Это сердце программы. Оно работает по следующему алгоритму:
# 1.  **Ожидание:** Скрипт ждет ваш текстовый запрос (например: *"Посмотри, какие файлы есть в этой папке, и прочитай файл main.py"*).
# 2.  **Анализ:** Запрос отправляется в LLM.
# 3.  **Вызов функции:** Нейросеть понимает, что ей не хватает данных для ответа, и возвращает технический JSON-запрос: *"Выполни команду `ls`"*.
# 4.  **Исполнение:** Ваш Python-скрипт перехватывает этот запрос, спрашивает у вас разрешение, выполняет `ls` в терминале и собирает результат (список файлов).
# 5.  **Возврат результата:** Скрипт незаметно для вас отправляет этот список файлов обратно в LLM со словами: *"Вот результат твоей команды"*.
# 6.  **Ответ:** Получив данные, нейросеть формулирует финальный, понятный ответ и выводит его вам на экран.

# ==========================================
# 1. КОНФИГУРАЦИЯ
# ==========================================
# Укажите свои данные или задайте их через переменные окружения
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "ВАШ_API_КЛЮЧ")
OPENAI_API_URL = os.getenv("OPENAI_API_URL", "ВАШ_API_URL")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "ВАШ_MODEL")

client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_API_URL,
)

# ==========================================
# 2. ОПИСАНИЕ ИНСТРУМЕНТОВ ДЛЯ МОДЕЛИ
# ==========================================
tools = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Читает содержимое локального файла.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Относительный или абсолютный путь к файлу"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell_command",
            "description": "Выполняет команду в терминале (bash/cmd). Используй для запуска тестов, поиска файлов и проверки окружения.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Команда для выполнения"}
                },
                "required": ["command"]
            }
        }
    },
    # НОВЫЙ ИНСТРУМЕНТ: Запись в файл
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Создает новый файл или полностью перезаписывает существующий. Используй для написания кода, конфигов или сохранения результатов.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Путь к файлу для записи"},
                    "content": {"type": "string", "description": "Полное текстовое содержимое, которое нужно записать в файл"}
                },
                "required": ["path", "content"]
            }
        }
    }
]

# ==========================================
# 3. ЛОКАЛЬНЫЕ ФУНКЦИИ (РУКИ АГЕНТА)
# ==========================================
def read_file(path):
    print(f"\n[📖 Агент читает файл: {path}]")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {e}"

def run_shell_command(command):
    print(f"\n[⚠️  Агент хочет выполнить команду: {command}]")
    confirm = input("Разрешить? (y/n/ввести свою команду): ")
    
    if confirm.lower() == 'n':
        return "System response: User denied permission."
    elif confirm.lower() not in ['', 'y', 'yes']:
        command = confirm
        print(f"[Выполняю вашу команду: {command}]")

    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        return output if output else "Command executed successfully."
    except Exception as e:
        return f"Error executing command: {e}"

# НОВАЯ ФУНКЦИЯ: Логика записи в файл
def write_file(path, content):
    print(f"\n[💾 Агент хочет записать/перезаписать файл: {path}]")
    # Показываем первые 100 символов, чтобы было понятно, что агент собирается писать
    preview = content[:100] + ("..." if len(content) > 100 else "")
    print(f"Превью содержимого:\n{preview}\n")
    
    confirm = input("Разрешить запись? (y/n): ")
    if confirm.lower() not in ['y', 'yes', '']:
        return "System response: User denied permission to write this file."

    try:
        # Создаем директории, если их еще нет (например, если путь src/utils/math.py)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"File {path} successfully written."
    except Exception as e:
        return f"Error writing to file: {e}"

# ==========================================
# 4. ГЛАВНЫЙ АГЕНТНЫЙ ЦИКЛ
# ==========================================
def main():
    print(f"🤖 {OPENAI_MODEL} запущен! Введите 'exit' для выхода.")
    
    messages = [
        {"role": "system", "content": "Ты AI-помощник разработчика. У тебя есть доступ к файловой системе. Ты можешь читать файлы, выполнять команды терминала и создавать/перезаписывать файлы. Всегда думай по шагам."}
    ]

    while True:
        user_input = input("\nВы: ")
        if user_input.lower() in ['exit', 'quit']:
            print("Завершение работы.")
            break
            
        messages.append({"role": "user", "content": user_input})
        
        while True:
            try:
                response = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=messages,
                    tools=tools
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
                        
                        # ОБНОВЛЕННЫЙ РОУТИНГ: добавили обработку write_file
                        if func_name == "read_file":
                            result = read_file(args.get("path"))
                        elif func_name == "run_shell_command":
                            result = run_shell_command(args.get("command"))
                        elif func_name == "write_file":
                            result = write_file(args.get("path"), args.get("content"))
                        else:
                            result = f"Error: Unknown function {func_name}"
                            
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": str(result)
                        })
                else:
                    print(f"\nАгент: {message.content}")
                    break 
                    
            except Exception as e:
                print(f"\n[Ошибка API]: {e}")
                break

if __name__ == "__main__":
    main()