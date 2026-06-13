import os
from dotenv import load_dotenv
if __name__ == '__main__':
    env_file = ".env"
    load_success = load_dotenv(dotenv_path=".env",verbose=True, override=False)
    print("环境加载是否成功：",load_success)
    print("文件是否存在:", os.path.exists(env_file))
    print("是否可读:", os.access(env_file, os.R_OK))
    print(os.environ.get("PYTHON_DOTENV_DISABLED"))
    print(os.environ.get("COPYCLAUDE_LLM_DEFAULT_MODEL"))