# Play Together Fruit Harvest Bot - File 1

Project nay da duoc doi sang dung flow thu hoach trai cay theo menu, dung voi mo ta trong file 1.

Bot se lam theo thu tu:

1. Tim va click `Nhà ta`
2. Tim va click `Có thể thu hoạch`
3. Trong popup, click `Thu hoạch trái`
4. Trong danh sach trai cay, tim cac dong:
   - Sau rieng
   - Khe
   - Tao duong
   - Dua
   - Dau
5. Tim button `Thu hoạch` nam tren cung dong va click
6. Neu chua thay trai muc tieu tren man hinh hien tai thi cuon xuong
7. Neu thay message `Túi đã đầy nên không thể thu hoạch` thi dung bot

File 2 se xu ly flow ban sau khi day tui. Hien tai bot dung o moc day tui.

## Cau truc thu muc

```text
autofarm/
|-- main.py
|-- coordinate_helper.py
|-- config.json
|-- requirements.txt
|-- templates/
|   |-- README.md
|   |-- fruits/
|   `-- ui/
`-- utils/
    |-- config.py
    |-- detector.py
    |-- geometry.py
    |-- hotkey.py
    |-- input_controller.py
    |-- logger.py
    |-- screen.py
    |-- timing.py
    `-- window.py
```

## Chay bot

```powershell
py -3 main.py
```

Nhan `ESC` de dung an toan.

## Lay toa do

```powershell
py -3 coordinate_helper.py
```

Khi helper dang chay:

- Dua chuot vao diem can lay
- Nhan `F6`
- Copy `point_ratio` hoac `fallback_click_ratio snippet` vao `config.json`

Bot moi uu tien dung ratio:

- `fallback_click_ratio`
- `hover_point_ratio`

Neu ban van muon dung pixel, code van support thong qua `fallback_click` va `window.reference_client_size`.

## Template can chuan bi

### Fruit labels

- `templates/fruits/durian_label.png`
- `templates/fruits/starfruit_label.png`
- `templates/fruits/sugar_apple_label.png`
- `templates/fruits/coconut_label.png`
- `templates/fruits/bean_label.png`

Moi file phai la anh nho, crop sat vao ten trai hoac vung nhan dien on dinh cua dong trai cay trong danh sach.

### UI templates

- `templates/ui/home_button.png`
- `templates/ui/available_to_harvest_button.png`
- `templates/ui/harvest_fruit_popup_button.png`
- `templates/ui/fruit_harvest_row_button.png`
- `templates/ui/bag_full_message.png`

Moi file phai duoc chup sat vao dung button/message.

## Luu y quan trong

- Giu nguyen kich thuoc cua so BlueStacks khi chup template va khi chay bot.
- Neu button khong duoc detect on dinh, hay dien `fallback_click_ratio`.
- Bot se cuon xuong de tim trai muc tieu neu tren man hinh hien tai chua thay.
- Sau khi bag full, bot se dung. Day la dung theo pham vi file 1.
