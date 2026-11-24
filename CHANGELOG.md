# История версий

## v0.84 — 2026-11-24
- HDRI White balance (Kelvin, 6500 K = unchanged) and Tint (green-magenta) in HDRI settings, next to Saturation (Expert mode): only the map changes color, Corona Sun keeps its own; saved in presets and kept on Prev / Next
- Window icon: the logo keeps its proportions (it was squeezed in the HDRI Library title bar)

## v0.83 — 2026-11-23
- Lighting presets belong to the scene: each .max keeps its own list, a new scene starts with an empty list
- On every change the presets are also copied to “SceneName.presets.json” in the scene folder; a scene without presets picks them up from that file
- “Save next to scene” is no longer needed and was removed; “From file...” opens in the project folder and also reads presets.json of earlier versions
- Presets of earlier versions stay in %APPDATA%\SkyRigStudio\presets.json; a one-time message says how to bring them into a scene

## v0.82 — 2026-11-23
- Fix: 3ds Max could crash right after choosing a fog for a camera
- Fix: “No fog” for a camera really removes the fog (an empty SRS_NoFog volume; Corona ignores an empty override slot)
- Fog defaults are exactly those of a new CoronaVolumeMtl (Absorption 186 on screen, Distance 0, Scattering black, Directionality 0, Step size 1 cm)
- Map slots next to the Absorption / Scattering / Emission colors: drag a map from the Slate Material Editor, click to pick one, right-click to clear
- Color swatches open the 3ds Max color picker (e.g. Corona Improved Picker) without blocking: Interactive updates while you pick
- “Open in Slate Material Editor”; fogs are kept in the scene even when not assigned
- Simpler fog section: always collapsed when the window opens, Emission settings off, no built-in fog presets, no Allow mixing / Enable caustics, short fog names

## v0.81 — 2026-11-23
- Fog section: any number of independent fogs (CoronaVolumeMtl) with all their parameters; one global fog (Global volume material) and/or a separate fog or no fog for each Corona camera (Global volume override); fog presets; lighting presets save fogs and camera assignments. Collapsed by default
- Switching maps (Prev / Next, library) searches the sun only when “Link azimuth to HDRI” is on: fast browsing on heavy scenes
- The window opens wider so nothing is cut off, and widens itself in Expert mode
- Mouse wheel over drop-down lists scrolls the window instead of changing the value

## v0.80 — 2026-11-19
- Corona Sky: Altitude (observer height in meters) and Ground color; both are saved in presets and restored when you switch HDRI ↔ Corona Sky

## v0.79 — 2026-11-19
- “Create at center” finds a sun that is hidden (or on a hidden layer) and shows it; a new sun goes to a visible layer “SkyRig Studio” when the current layer is hidden
- Corona Sky: cloud parameters and “New clouds” appear only when Clouds is on
- Corona Sky: no more “sun is not linked to the brightest point of the HDRI” warning
- Small SkyRig Studio logo in the window header

## v0.78 — 2026-11-19
- Corona Sky: Exposure and Direct / Reflect / Refract EV, like the HDRI settings (the sky goes through a Color Correct; the EV channels use the same target as HDRI, shown in Expert mode)
- Switching between HDRI and Corona Sky moves the EV channels with it and restores the sky's Exposure and EV; presets remember them
- Scenes from 0.77 with a bare Corona Sky get the Color Correct on the first edit

## v0.77 — 2026-11-19
- Corona Sky mode: switch between an HDRI and the procedural Corona Sky; the sky follows the Corona Sun (Intensity, Turbidity, clouds, Morning / Noon / Evening / Sunset); the last HDRI and its settings are remembered in the scene; presets remember the mode
- Sun search finds the brightest spot instead of the brightest pixel: sunsets behind clouds no longer lock onto a street lamp or a window
- Prev / Next keep the Exposure you set
- Click anywhere on the Rotation slider to jump to that angle
- The library window closes after Apply to scene / double-click
- Downloading a map that is already in the library asks first: skip, download again, keep both resolutions or replace

## v0.76 — 2026-10-25
- Dates in the window (e.g. the auto-backup status) are always in English and no longer follow the Windows language
- README: credits for Poly Haven and ambientCG, the HDRI sources

## v0.75 — 2026-10-25
- The sun follows HDRI rotation reliably: the “Link azimuth to HDRI” state is saved in the scene, and on maps without a distinct sun the sun turns together with the map
- Your own map in a Direct / Reflect / Refract override slot is no longer replaced silently: when you change that EV, the script asks first
- Horizon angle and the Horizon line switch share one row; Blur and Horizon rows are aligned
- Fixed the up-down cursor getting stuck over the window after dragging spinner arrows
- Behance and Instagram buttons next to Donate

## v0.74 — 2026-10-21
- Maps without a distinct sun (night, overcast, dusk) no longer move the sun when switched with Prev/Next or rotated with the link on; “link to HDRI” places it without asking and notes it in the status line
- Horizon line switch in HDRI settings: shows the horizon in the camera of the current view (Corona Camera, Physical, Target/Free Camera)

## v0.73 — 2026-10-21
- New HDRI Library layout: controls grouped by task (top bar: add, download, search, filter, sort, size, Library menu; groups on the left; actions for selected maps above the grid; inspector on the right)
- ★ Favorites: star maps from the selection bar or the right-click menu; they appear in the pinned Favorites group
- Maps without a distinct sun (night, overcast, dusk) no longer move the sun when switched with Prev/Next or rotated with the link on; “link to HDRI” asks first
- Horizon line switch in HDRI settings: shows the horizon in the camera of the current view (Corona Camera, Physical, Target/Free Camera)
- Previews no longer disappear when an HDRI is moved, renamed, re-downloaded or touched by OneDrive/antivirus (preview name now comes from the file content)
- Automatic backups of the library and previews (Documents\SkyRig Studio\Backups), Backup & Restore window, banner to restore missing previews
- ☀ Detect sun... (selected / all maps): the sun position is found in advance, and the sun is aligned automatically when a map is applied
- Lighting tags are added automatically while previews are created; manual Tags... and Delete previews buttons removed
- Data moved to %APPDATA%\SkyRigStudio (important) and %LOCALAPPDATA%\SkyRigStudio (cache); old SafeEnvironment data is copied automatically

## v0.72 — 2026-10-14
- Full English interface (English only)
- Old Russian tags migrate automatically
- English installer

## v0.70 — 2026-10-13
- Кнопка Donate (PayPal) и почта автора внизу окна
- Окно нельзя сделать уже 288 px: кнопки больше не уходят за край
- В списке «Каналы EV в» вместо «Активная CoronaCam» показывается полное имя активной камеры

## v0.60 — 2026-10-13
- Журнал действий last_actions.log (пункт в меню ▾) для разбора сбоев
- Убрана строка «для внутреннего использования» из подписи автора

## v0.58 — 2026-10-13
- Название пресета при сохранении: подставляется имя активной камеры, остаётся дописать вариант

## v0.57 — 2026-10-13
- Новое название: SkyRig Studio
- Установщик и кнопка в Max переименованы; старая установка SafeEnvironment заменяется автоматически, библиотека и пресеты сохраняются

## v0.56 — 2026-10-13
- Переключатель режимов: Simple / Expert
- Кнопка Start Interactive Render перенесена под «Библиотека / Перезагрузить HDR»

## v0.55 — 2026-10-13
- Новое оформление: единая палитра, карточки, значки на кнопках
- Режимы «Просто» / «Расширенный» и сворачиваемые секции
- Тумблеры вместо флажков
- Установщик: окно с кнопками «Запустить / Удалить скрипт / Закрыть», кнопка Uninstall в категории SafeEnvironment

## v0.51 — 2026-10-13
- Окно «Доступно обновление» с выбором «Обновить» / «Отмена»
- Сообщение, если установлена последняя версия
- Подписи кнопок в окнах обновления и отката на русском

## v0.50 — 2026-10-13
- Первая публикация: библиотека HDRI, Corona Sun, пресеты, выравнивание яркости, загрузка карт с ambientCG и Poly Haven, обновление по кнопке
