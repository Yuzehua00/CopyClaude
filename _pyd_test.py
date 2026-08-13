from pydantic import BaseModel
from typing import Literal

class E(BaseModel):
    type: Literal['session.send_message'] = 'session.waiting_for_input'

e = E()
print('INSTANCE type =', repr(e.type))
print('FIELD default  =', repr(E.model_fields['type'].default))
print('FIELD annotation =', E.model_fields['type'].annotation)

# 模拟 core 发布事件（不传 type，用默认值）
print('--- publish without type ---')
print('type sent =', repr(e.type))

# 模拟传入 waiting_for_input
try:
    e2 = E(type='session.waiting_for_input')
    print('explicit waiting_for_input OK ->', repr(e2.type))
except Exception as ex:
    print('explicit waiting_for_input ERROR ->', type(ex).__name__, ex)

# 模拟传入 send_message
try:
    e3 = E(type='session.send_message')
    print('explicit send_message OK ->', repr(e3.type))
except Exception as ex:
    print('explicit send_message ERROR ->', type(ex).__name__, ex)
