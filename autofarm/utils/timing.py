import random
import time


def random_in_range(value) -> float:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return random.uniform(float(value[0]), float(value[1]))
    return float(value)


def sleep_random(value) -> None:
    time.sleep(random_in_range(value))
