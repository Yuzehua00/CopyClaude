# 处理cli/main中用户命令为copyclaude --version的情况。返回当前的版本号，当前版本号字符串位于__init__.py中。
import copy_claude

def cmd_version()->None:
    print(copy_claude.__version__)