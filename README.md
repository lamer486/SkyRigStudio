# SkyRig Studio

**Finds the sun on any HDRI map and links Corona Sun to it — in one click.**

No more guessing Height and Azimuth until the shadow "matches" the map: SkyRig Studio measures the HDRI brightness, finds
the brightest point (the sun on the map) and aims Corona Sun in exactly the same direction — taking the map rotation into account.
For 3ds Max + Corona Renderer. *Русская версия описания — ниже ([перейти](#skyrig-studio--по-русски)).*

<p align="center">
  <img src="docs/images/main-expert-2.png" width="300" alt="link to HDRI button, azimuth link and sun compass">
</p>

## How it works
1. **Pick an HDRI** — from your own library, or download a free map right in the window (ambientCG, Poly Haven).
2. **Click "link to HDRI"** — the script finds the brightest point of the map (`.hdr` and `.exr`, including large 16K ones; progress
   is shown on the indicator) and sets the sun's Height and Azimuth so it matches the sun on the map.
3. **Rotate the map freely** — with **"Link azimuth to HDRI"** enabled the sun azimuth is recalculated automatically, so the light
   from the map and Corona Sun always point the same way.
4. **Check it on the compass** — a sphere textured with the HDRI shows where the sun is on the map, where Corona Sun is and where the camera looks from.

Without the link the sun stays where you put it; you can click "link to HDRI" again at any time.

## What else it does
- **HDRI library** — groups, tags, search, previews, resolution label (e.g. `[8k]`), automatic lighting-type detection (Morning … Night, Overcast).
- **Map controls** — rotation, Exposure, Gamma, Saturation, Blur, Horizon, separate Direct / Reflect / Refract EV,
  "Prev / Next" map switching (with the link on, the sun jumps to the bright point of the new map). The EV channels can target
  the global Corona settings or the active CoronaCam (shown by name).
- **Brightness balancing** — brings maps to one lighting level relative to a reference map, so the light doesn't "jump" when you switch HDRIs.
- **Presets** — the whole lighting setup (map, parameters, sun); the active camera name is suggested when saving.
- **Free map downloads** — ambientCG and Poly Haven (CC0), resolution choice, file integrity checks.
- **Own history** — "Undo / Redo" for all script actions.
- **Simple / Expert modes** — essentials only, or every parameter.
- **Reliability** — the script does nothing "in the background" (no callbacks, timers or threads); every action is a single undo step.
- **One-button updates** — with confirmation and rollback to the previous version.
- **Sun compass** — click the sphere to place the sun; the HDRI rotates with it.

<p align="center">
  <img src="docs/images/main-expert-1.png" width="260" alt="Main window, Expert mode">
  <img src="docs/images/main-simple.png" width="260" alt="Simple mode">
</p>

## Screenshots
**HDRI library**

<img src="docs/images/library.png" width="760" alt="HDRI library">

**Downloaders: ambientCG** and **Poly Haven**

<img src="docs/images/ambientcg.png" width="760" alt="ambientCG downloader">

<img src="docs/images/polyhaven.png" width="760" alt="Poly Haven downloader">

## Requirements
- 3ds Max **2025 or newer** (PySide6 required; tested on 3ds Max 2027).
- Corona Renderer (tested on Corona 16).
- Windows. Internet access is needed for updates and map downloads (the script itself works offline).

## Installation
1. Download [`dist/SkyRigStudio_v0.74.mzp`](https://github.com/lamer486/SkyRigStudio/raw/main/dist/SkyRigStudio_v0.74.mzp).
2. Drag the file into the 3ds Max window (any viewport). The installer copies the script and creates a macro.
3. Put the button on a toolbar: **Customize → Customize User Interface → Toolbars** (or Menus / Quads) →
   category **SkyRig Studio** → drag "SkyRig Studio" onto a toolbar. Launch the script with that button.

The window docks on the left side of the Max interface (you can drag it to the right edge or float it). Minimum width is 288 px.

## Updating
At the top of the window there is a **"Check for updates"** button. Once a day the script quietly checks the version itself; if a new one is out,
the button turns green — **"Update to vX.Y"**. Click it, read the change list and confirm:
the file is downloaded and verified (SHA-256 checksum, syntax, version number), and the window restarts.
The previous version is kept — menu **"▾" → "Restore previous version"**. Auto-check can be turned off there too.

## Uninstalling
In Max: **Customize → Customize User Interface → Toolbars**, category **SkyRig Studio**, button **"Uninstall SkyRig Studio"**
(drag it to a toolbar and click it) — or drag `dist/SkyRigStudio_Uninstall.mzp` into the Max window. The library, presets and cache are stored separately in
`%APPDATA%\SafeEnvironment` — the uninstaller asks whether to delete them.

## Where things are stored
| What | Where |
|---|---|
| Script | `%LOCALAPPDATA%\Autodesk\3dsMax\<version> - 64bit\ENU\scripts\SkyRigStudio\` |
| Library, presets, sun cache, action log, script version backups | `%APPDATA%\SkyRigStudio\` |
| Previews and site catalogs (cache, can be rebuilt) | `%LOCALAPPDATA%\SkyRigStudio\` |
| Library and preview backups | `Documents\SkyRig Studio\Backups\` (can be changed in **Backup & Restore**) |
| 3ds Max scene | the script writes nothing into it except the environment itself (Corona Color Correct → CoronaBitmap) and one Corona Sun |

Versions before 0.73 kept everything in `%APPDATA%\SafeEnvironment\`. On the first start of v0.73 the data is copied
to the new folders automatically; the old folder is left in place.

**Backups.** The library and previews are backed up automatically after previews are created, after the library changes
(at most every 10 minutes) and when the library window is closed. The last 10 backups are kept. If previews go missing,
the library shows a banner with **Restore from backup**. HDRI files themselves are not copied into backups.

**HDRI Library layout (v0.73).** Top bar: *+ Add*, *⬇ Download*, search, tag filter, sort, thumbnail size and the
*Library* menu (backup, restore, save/load library file). Left: groups with *★ Favorites* pinned on top. Above the grid:
actions for the selected maps (add to group, remove, ★ Favorites) — shown only while something is selected; right-click
a map for the same actions. Right: inspector — the selected map (resolution, lighting, sun, brightness correction),
exposure balance and previews.

**Sun positions in advance.** **☀ Detect sun…** in the library (on selected or on all maps) finds the sun on each map once.
When such a map is applied, the Corona Sun is aligned with it immediately (option *Align the sun with the HDRI when a map
is applied*); maps without a distinct sun (overcast, night) leave the sun where it is. Found maps are marked with ☀.

The library stores **full paths** to HDRIs, so on another computer it starts empty; to move it use the
"Save library to file…" / "Load library from file…" buttons (the HDRI files themselves are not copied).

## Internet and privacy
The script only connects: (1) once a day to `raw.githubusercontent.com` for the version number (can be turned off),
(2) on your command — to `ambientcg.com` and `polyhaven.com` for the catalog and maps (CC0 license), (3) on the "Update" button —
for the new script file. No data about you or your scene is sent anywhere.

## Support the author
Script created with the help of Claude AI by Vasiliy Kanevskiy, 2026. If it saves you time: [♥ Donate via PayPal](https://paypal.me/Vkanevskiy).
Contact: kanevskiy.vasiliy@gmail.com

---

# SkyRig Studio — по-русски

**Находит солнце на любой HDRI-карте и привязывает к нему Corona Sun — одним нажатием.**

Больше не нужно вручную подбирать высоту и азимут, пока тень «не совпадёт» с картой: SkyRig Studio измеряет яркость
HDRI, находит самую яркую точку (солнце на карте) и ставит Corona Sun ровно в то же направление — с учётом поворота карты.
Для 3ds Max + Corona Renderer. Интерфейс скрипта — на английском.

<p align="center">
  <img src="docs/images/main-expert-2.png" width="300" alt="Кнопка «link to HDRI», привязка азимута и компас солнца">
</p>

## Как это работает
1. **Выберите HDRI** — из своей библиотеки или скачайте бесплатную карту прямо в окне (ambientCG, Poly Haven).
2. **Нажмите «link to HDRI»** — скрипт находит самую яркую точку карты (`.hdr` и `.exr`, в том числе большие, 16K; ход поиска виден
   на индикаторе) и выставляет Height и Azimuth солнца так, чтобы оно совпало с солнцем на карте.
3. **Вращайте карту как угодно** — при включённой **«Link azimuth to HDRI»** азимут солнца пересчитывается сам, и свет с
   карты и Corona Sun всегда направлены в одну сторону.
4. **Проверьте на компасе** — шар с текстурой HDRI показывает, где солнце на карте, где Corona Sun и откуда смотрит камера.

Без привязки солнце остаётся там, где вы его поставили; в любой момент можно нажать «link to HDRI» ещё раз.

## Что ещё умеет
- **Библиотека HDRI** — группы, теги, поиск, превью, подпись с разрешением (например, `[8k]`), автоопределение типа освещения.
- **Управление картой** — поворот, Exposure, Gamma, Saturation, Blur, Horizon, отдельные EV для Direct / Reflect / Refract,
  переключение карт «Prev / Next» (при включённой привязке солнце переставляется на яркую точку новой карты).
- **Выравнивание яркости** — приводит карты к единому уровню освещённости по эталонной, чтобы при смене HDRI свет не «прыгал».
- **Пресеты** — освещение целиком (карта, параметры, солнце); при сохранении подставляется имя активной камеры.
- **Загрузка карт из интернета** — ambientCG и Poly Haven (лицензия CC0), выбор разрешения, проверка целостности файлов.
- **Своя история изменений** — «Undo / Redo» для всех действий скрипта.
- **Режимы Simple / Expert** — только основное или все параметры.
- **Надёжность** — скрипт ничего не делает «в фоне» (без callbacks, таймеров и потоков), каждое действие — один undo-блок.
- **Обновление одной кнопкой** — с подтверждением и откатом к предыдущей версии.

<p align="center">
  <img src="docs/images/main-expert-1.png" width="260" alt="Главное окно, режим Expert">
  <img src="docs/images/main-simple.png" width="260" alt="Режим Simple">
</p>

## Скриншоты
**Библиотека HDRI**

<img src="docs/images/library.png" width="760" alt="Библиотека HDRI">

**Загрузка с ambientCG** и **Poly Haven**

<img src="docs/images/ambientcg.png" width="760" alt="Загрузчик ambientCG">

<img src="docs/images/polyhaven.png" width="760" alt="Загрузчик Poly Haven">

## Требования
- 3ds Max **2025 или новее** (нужен PySide6; проверено на 3ds Max 2027).
- Corona Renderer (проверено на Corona 16).
- Windows. Для обновлений и загрузки карт — доступ в интернет (сам скрипт работает и без него).

## Установка
1. Скачайте [`dist/SkyRigStudio_v0.74.mzp`](https://github.com/lamer486/SkyRigStudio/raw/main/dist/SkyRigStudio_v0.74.mzp).
2. Перетащите файл мышью в окно 3ds Max (в любой вьюпорт). Установщик скопирует скрипт и создаст кнопку.
3. Выведите кнопку на панель: **Customize → Customize User Interface → Toolbars** (или Menus / Quads) →
   категория **SkyRig Studio** → перетащите «SkyRig Studio» на панель. Запуск — по этой кнопке.

Окно встраивается слева в интерфейс Max (его можно перетащить к правому краю или оторвать).

## Обновление
В верху окна есть кнопка **«Check for updates»**. Раз в сутки скрипт тихо проверяет версию сам; если вышла новая,
кнопка становится зелёной — **«Update to vX.Y»**. Нажмите её, прочтите список изменений и подтвердите:
файл скачивается, проверяется (контрольная сумма sha256, синтаксис, номер версии), и окно перезапускается.
Предыдущая версия сохраняется — меню **«▾» → «Restore previous version»**. Автопроверку можно отключить там же.

## Удаление
В Max: **Customize → Customize User Interface → Toolbars**, категория **SkyRig Studio**, кнопка **«Uninstall SkyRig Studio»**
(перетащите её на панель и нажмите) — или перетащите в окно Max `dist/SkyRigStudio_Uninstall.mzp`. Библиотека, пресеты и кэш лежат отдельно в
`%APPDATA%\SafeEnvironment` — установщик спросит, удалять ли их.

## Где что хранится
| Что | Где |
|---|---|
| Скрипт | `%LOCALAPPDATA%\Autodesk\3dsMax\<версия> - 64bit\ENU\scripts\SkyRigStudio\` |
| Библиотека, пресеты, кэш, резервные копии версий | `%APPDATA%\SafeEnvironment\` |
| Сцена 3ds Max | скрипт ничего в неё не записывает, кроме самого окружения (Corona Color Correct → CoronaBitmap) и одного Corona Sun |

Библиотека хранит **полные пути** к HDRI, поэтому на другом компьютере она начинается пустой; перенос — кнопки
«Save library to file…» / «Load library from file…» (сами HDRI при этом не копируются).

## Интернет и приватность
Скрипт обращается в сеть только: (1) раз в сутки к `raw.githubusercontent.com` за номером версии (можно выключить),
(2) по вашей команде — к `ambientcg.com` и `polyhaven.com` за каталогом и картами (лицензия CC0), (3) по кнопке «Обновить» —
за новым файлом скрипта. Никаких данных о вас или сцене не отправляется.

---

## Поддержать автора
Скрипт создан с помощью Claude AI, автор — Василий Каневский, 2026. Если скрипт экономит вам время: [♥ Donate через PayPal](https://paypal.me/Vkanevskiy). Почта: kanevskiy.vasiliy@gmail.com

---

## Для владельца репозитория: как выпустить новую версию
Нужен Python 3 (подойдёт любой). В корне репозитория:

```
python tools/make_release.py --src skyrig_studio_v0.51.py --notes "Что нового 1" "Что нового 2"
```

Скрипт проверит файл, пропишет адрес обновлений, положит копию в `releases/`, посчитает контрольную сумму, обновит
`version.json`, `CHANGELOG.md` и пересоберёт `dist/*.mzp`. Затем загрузите изменённые файлы в репозиторий (`git add -A && git commit && git push`
или «Add file → Upload files» на сайте GitHub). Пользователи увидят обновление в течение суток (или сразу по кнопке). GitHub кэширует файлы до ~5 минут, так что сразу
после загрузки проверка может ещё показывать старую версию — это нормально.

Важно: `version.json` и файл из `releases/` должны попадать в репозиторий **вместе** — контрольная сумма в манифесте
привязана к файлу; если они разойдутся, скрипты у пользователей откажутся обновляться (это защита).

Первый выпуск (один раз, указываем логин и имя репозитория):
```
python tools/make_release.py --src skyrig_studio_v0.50.py --repo ВАШ_ЛОГИН/SkyRigStudio --notes "Первая публикация"
```

Репозиторий должен быть **публичным** (иначе `raw.githubusercontent.com` не отдаёт файлы без токена).
Адрес манифеста можно переопределить без правки скрипта: положите его в первую строку файла
`%APPDATA%\SafeEnvironment\update_url.txt` (например, для тестовой ветки или файла на сетевой папке).
