import os
from dotenv import dotenv_values

__all__ = [
    'ENV',
]

def my_bool(value):
    if isinstance(value, bool):
        return value
    if value.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif value.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise ValueError(f'Invalid boolean value: {value}')

ENV = {
    **dotenv_values('.env.example'),
    **dotenv_values('.env'),
    **os.environ,
}

# process booleans
for key in [
    'DEBUG',
]:
    ENV[key] = my_bool(ENV.get(key, False))
