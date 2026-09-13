from machine import Pin
import time

LEDS = 12
ROWS = 4
COLS = 4

keys = (
    ("1", "2", "3", "A"),
    ("4", "5", "6", "B"),
    ("7", "8", "9", "C"),
    ("*", "0", "#", "D"),
)

led_pins = [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 28, 27]
row_pins = [26, 22, 21, 20]
col_pins = [19, 18, 17, 16]

leds = [Pin(pin, Pin.OUT, value=0) for pin in led_pins]
rows = [Pin(pin, Pin.OUT, value=1) for pin in row_pins]
cols = [Pin(pin, Pin.IN, Pin.PULL_UP) for pin in col_pins]


def scan_key():
    for row_index, row in enumerate(rows):
        for other_row in rows:
            other_row.value(1)

        row.value(0)

        for col_index, col in enumerate(cols):
            if col.value() == 0:
                row.value(1)
                return keys[row_index][col_index]

    return None


def process_key(key):
    if "1" <= key <= "8":
        leds[ord(key) - ord("1")].value(1)

    elif key == "9":
        for led in leds[:8]:
            led.value(1)

    elif key == "0":
        for led in leds[:8]:
            led.value(0)

    elif key in ("A", "B", "C", "D"):
        leds[8 + ord(key) - ord("A")].value(1)

    elif key == "*":
        for led in leds[8:12]:
            led.value(1)

    elif key == "#":
        for led in leds[8:12]:
            led.value(0)


last_key = None

while True:
    key = scan_key()

    if key != last_key:
        if key is not None:
            process_key(key)
        last_key = key

    time.sleep_ms(10)
