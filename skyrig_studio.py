# -*- coding: utf-8 -*-
# SkyRig Studio. Script created with the help of Claude AI by Vasiliy Kanevskiy, 2026.
"""
SkyRig Studio v0.2 — окружение (HDRI + солнце) для 3ds Max 2027 + Corona.

Что умеет
  * превью текущего HDRI; клик по превью открывает библиотеку на локальном диске:
    свои группы (создать / переименовать / удалить), добавление и удаление файлов
    .hdr/.exr, поиск, миниатюры;
  * поворот HDRI на любой угол, Exposure (EV), Gamma, Saturation;
  * создание Corona Sun в центре сцены; выравнивание солнца по самой яркой точке HDRI
    (с учётом поворота HDRI) и, по желанию, «привязка» — солнце следует за поворотом;
  * размер, интенсивность, высота над горизонтом и азимут солнца;
  * пресеты освещения (HDRI + все параметры + солнце) под своим именем.

Принципы надёжности (скрипт писался с нуля из-за вылетов Max)
  * НЕТ callbacks, таймеров, потоков, `when`-обработчиков, persistent global и
    скриптовых контроллеров — скрипт ничего не делает «в фоне» и не слушает Max;
    открытое окно в простое ничего не опрашивает (важно при работе интерактивного рендера);
  * скрипт не хранит ссылки на объекты сцены — всё ищется заново при каждом действии;
  * в сцене создаются только: цепочка Color Correction -> CoronaBitmap в слоте Scene Environment -> Single map (Corona)
    и один CoronaSun; никаких помощников;
  * интернет — только по действию пользователя (скачивание карт с ambientCG / Poly Haven, кнопка обновления)
    и раз в сутки лёгкая проверка версии (отключается в меню «▾» под названием); данные библиотеки/пресетов
    лежат ВНЕ сцены: %APPDATA%\\SafeEnvironment;
  * каждое действие — один undo-блок и обёрнуто в try/except.

Запуск: Scripting -> Run Script... -> выбрать этот файл.
"""

import array
import glob
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
import re
import struct
import sys
import time
import traceback

from pymxs import runtime as rt
import pymxs

from PySide6 import QtWidgets, QtCore, QtGui
import qtmax

VERSION = "0.73"
WINDOW_TITLE = "SkyRig Studio v" + VERSION
COPYRIGHT_TEXT = "Script created with the help of Claude AI by Vasiliy Kanevskiy, 2026."
CONTACT_EMAIL = "kanevskiy.vasiliy@gmail.com"
DONATE_URL = "https://paypal.me/Vkanevskiy"
DONATE_TEXT_BTN = "♥ Donate"
WINDOW_OBJ_NAME = "SafeEnvironmentWindow"
DOCK_OBJ_NAME = "SafeEnvironmentDock"
LIBRARY_OBJ_NAME = "SafeEnvironmentLibrary"

SUN_NAME = "EnvSun"
HDRI_EXTS = (".hdr", ".exr")
BACKUP_FORMAT = "SafeEnvironmentLibrary"
PRESETS_FORMAT = "SafeEnvironmentPresets"
ALL_GROUP = "All files"              # виртуальная группа: объединение всех групп
FAV_GROUP = "★ Favorites"            # виртуальная группа: избранные карты (v0.73)
THUMB_W, THUMB_H = 256, 128
PREVIEW_W, PREVIEW_H = 200, 100        # превью в главном окне (компактное)
DOCK_WIDTH = 288                       # ширина док-панели (и минимальная: уже — кнопки уходят за край)
THUMB_GAMMA = 1.0                     # гамма превью (1.0 = без гамма-кривой)
THUMB_EV_RANGE = 4.0                  # ползунок экспозиции превью: ±EV
ICON_NAME = "EnvSunIcon"
ICON_SIZE_DEFAULT = 12.0              # метры; примерно как тело Corona Camera
SUN_DIST_DEFAULT = 300.0              # метры
SUN_DIST_MIN, SUN_DIST_MAX = 1, 5000  # метры (диапазон поля «Расстояние до цели»)
SUN_MIN_PEAK = 20.0                   # ниже этой яркости «пик» вряд ли солнце

# Где что лежит (v0.73+):
#   %APPDATA%\SkyRigStudio        — важные данные: библиотека, пресеты, кэш солнца, журнал, копии версий скрипта;
#   %LOCALAPPDATA%\SkyRigStudio   — кэш, который можно пересоздать: превью, каталоги и миниатюры сайтов;
#   Документы\SkyRig Studio\Backups — автобэкапы библиотеки и превью (папку можно сменить в Backup & Restore).
# До v0.73 всё лежало в %APPDATA%\SafeEnvironment — при первом запуске данные переносятся (см. migrate_legacy_data).
_APPDATA = os.environ.get("APPDATA") or os.path.expanduser("~")
LEGACY_DATA_DIR = os.path.join(_APPDATA, "SafeEnvironment")
DATA_DIR = os.path.join(_APPDATA, "SkyRigStudio")
LOCAL_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or _APPDATA, "SkyRigStudio")
THUMB_DIR = os.path.join(LOCAL_DIR, "thumbs")
THUMB_INDEX_FILE = os.path.join(LOCAL_DIR, "thumb_index.json")
LIB_FILE = os.path.join(DATA_DIR, "library.json")
PRESET_FILE = os.path.join(DATA_DIR, "presets.json")
CACHE_FILE = os.path.join(DATA_DIR, "cache.json")
ACTION_LOG = os.path.join(DATA_DIR, "last_actions.log")


# ==========================================================================
# 1. Чистая математика (не зависит от 3ds Max; проверена калибровкой в Corona)
# ==========================================================================
def sun_basis(elevation_deg, azimuth_deg):
    """Базис света, излучающего вдоль -Z.
    elevation — угол над горизонтом (0..90), azimuth — направление НА солнце,
    от +Y (север) по часовой стрелке: 0 = +Y, 90 = +X, 180 = -Y, 270 = -X.
    Возвращает (x, y, z); z направлен на солнце."""
    e = math.radians(max(0.0, min(90.0, elevation_deg)))
    a = math.radians(azimuth_deg)
    z = (math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e))
    x = (-z[1], z[0], 0.0)
    ln = math.hypot(x[0], x[1])
    x = (1.0, 0.0, 0.0) if ln < 1e-9 else (x[0] / ln, x[1] / ln, 0.0)
    y = (z[1] * x[2] - z[2] * x[1],
         z[2] * x[0] - z[0] * x[2],
         z[0] * x[1] - z[1] * x[0])
    return x, y, z


def angles_from_z(z):
    ze = max(-1.0, min(1.0, z[2]))
    return math.degrees(math.asin(ze)), math.degrees(math.atan2(z[0], z[1])) % 360.0


# Horizon: смещение карты по вертикали = CoronaBitmap.uvwOffset.y. Масштаб и знак ниже НЕ откалиброваны
# рендером: при необходимости меняются тут (HORIZON_OFFSET_SIGN — направление сдвига карты,
# HORIZON_ELEV_SIGN — как сдвиг меняет высоту света, DEG_PER_UNIT — градусов на единицу offset V).
HORIZON_MIN, HORIZON_MAX = -90.0, 90.0
HORIZON_DEG_PER_UNIT = 180.0
HORIZON_OFFSET_SIGN = 1.0
HORIZON_ELEV_SIGN = 1.0


def bm_horizon(bm):
    """Сдвиг горизонта карты в градусах (из uvwOffset.y)."""
    try:
        return HORIZON_OFFSET_SIGN * float(bm.uvwOffset.y) * HORIZON_DEG_PER_UNIT
    except Exception:
        return 0.0


def bm_set_horizon(bm, deg):
    o = bm.uvwOffset
    y = HORIZON_OFFSET_SIGN * float(deg) / HORIZON_DEG_PER_UNIT
    if abs(float(o.y) - y) > 1e-9:
        bm.uvwOffset = rt.point3(float(o.x), y, float(o.z))


def hdri_pixel_to_direction(u, v, rotation_deg, horizon_deg=0.0):
    """Точка (u, v) равнопромежуточной HDRI -> (азимут, высота) в градусах.

    ИЗМЕРЕНО в Corona (CoronaBitmap, Spherical) на тестовых картах с маркерами при
    Rotation = 0, 37, 90, 200, -50, 725: погрешность <= 0.11 градуса.
        азимут  = -360 * u - Rotation   (компас: от +Y по часовой)
        высота  = (0.5 - v) * 180
    """
    az = (-360.0 * u - rotation_deg) % 360.0
    el = (0.5 - v) * 180.0 + HORIZON_ELEV_SIGN * float(horizon_deg)
    return az, el


def ev_to_gain_percent(ev):
    """Экспозиция (стопы) -> Gain в процентах (100 = без изменений).
    Измерено: при lightnessMode = 1 Gain 200 даёт ровно ×2."""
    return 100.0 * (2.0 ** ev)


def gain_percent_to_ev(gain):
    return math.log(max(gain, 1e-6) / 100.0, 2.0)


# ==========================================================================
# 2. Хранилища (JSON вне сцены)
# ==========================================================================
_ALOG_MAX = 256 * 1024


def alog(msg):
    """Журнал действий (%APPDATA%\\SafeEnvironment\\last_actions.log): строка пишется и закрывается сразу, поэтому после
    сбоя Max в журнале остаётся последнее действие. Никогда не выбрасывает исключений и ничего не слушает."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            if os.path.getsize(ACTION_LOG) > _ALOG_MAX:               # ограничение размера: старый журнал -> .1
                os.replace(ACTION_LOG, ACTION_LOG + ".1")
        except OSError:
            pass
        line = "%s  %s\n" % (time.strftime("%H:%M:%S") + ".%03d" % (int(time.time() * 1000) % 1000), str(msg)[:400])
        with open(ACTION_LOG, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except Exception:
        try:                       # битый файл — сохраняем копию и начинаем заново
            os.replace(path, path + ".broken")
        except Exception:
            pass
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)          # атомарная замена: недописанный файл не появится


def norm_path(p):
    return os.path.normpath(p)


def path_key(p):
    return os.path.normcase(os.path.normpath(p))


def file_signature(p):
    try:
        st = os.stat(p)
        return "%s|%d|%d" % (path_key(p), int(st.st_mtime), st.st_size)
    except OSError:
        return "%s|0|0" % path_key(p)


# ---------- имя превью: по содержимому файла (v0.73) ----------
# До v0.73 имя превью считалось от пути + даты изменения + размера. Стоило перенести/переименовать карту, заново
# скачать её или дать OneDrive/антивирусу «тронуть» дату — и превью «пропадало» (файл оставался, но под старым именем).
# Теперь имя = хэш размера и первых/последних 64 КБ файла: не зависит от пути, имени и даты. Чтобы не читать файл
# при каждом обращении, найденное имя запоминается по подписи (путь|дата|размер) в thumb_index.json.
THUMB_VER = "t5"
_FP_CHUNK = 64 * 1024
_THUMB_INDEX = None          # подпись -> имя превью (загружается при первом обращении)
_THUMB_INDEX_DIRTY = False
_LEGACY_CHECKED = set()      # имена, для которых уже проверили превью старого формата


def _thumb_index():
    global _THUMB_INDEX
    if _THUMB_INDEX is None:
        d = load_json(THUMB_INDEX_FILE, {})
        _THUMB_INDEX = d.get("ids", {}) if isinstance(d, dict) and isinstance(d.get("ids"), dict) else {}
    return _THUMB_INDEX


def save_thumb_index():
    """Записывает индекс имён превью, если он менялся. Вызывается после пакетных операций и при закрытии библиотеки."""
    global _THUMB_INDEX_DIRTY
    if not _THUMB_INDEX_DIRTY or _THUMB_INDEX is None:
        return
    idx = _THUMB_INDEX
    if len(idx) > 20000:                                  # не растём бесконечно: старые подписи отбрасываются
        for k in list(idx.keys())[:5000]:
            idx.pop(k, None)
    try:
        save_json(THUMB_INDEX_FILE, {"version": 1, "ids": idx})
        _THUMB_INDEX_DIRTY = False
    except Exception:
        pass


def content_id(p):
    """Имя превью по содержимому файла или None, если файл не читается."""
    global _THUMB_INDEX_DIRTY
    sig = file_signature(p)
    idx = _thumb_index()
    cid = idx.get(sig)
    if cid:
        return cid
    try:
        size = os.path.getsize(p)
        h = hashlib.sha1(("%d|%s|" % (size, THUMB_VER)).encode("ascii"))
        with open(p, "rb") as fh:
            h.update(fh.read(_FP_CHUNK))
            if size > 2 * _FP_CHUNK:
                fh.seek(-_FP_CHUNK, os.SEEK_END)
                h.update(fh.read(_FP_CHUNK))
        cid = h.hexdigest()[:24]
    except OSError:
        return None
    idx[sig] = cid
    _THUMB_INDEX_DIRTY = True
    return cid


def legacy_thumb_name(p):
    """Имя превью в версиях до v0.73 (путь + дата + размер) — только для переноса старого кэша."""
    return hashlib.sha1(("%s|t4" % file_signature(p)).encode("utf-8", "replace")).hexdigest()[:24]


def thumb_name(p):
    cid = content_id(p)
    if not cid:                                   # файла нет: имя, под которым превью заведомо не найдётся
        return "missing_" + hashlib.sha1(path_key(p).encode("utf-8", "replace")).hexdigest()[:16]
    if cid not in _LEGACY_CHECKED:                # превью старого формата -> переименовываем, а не создаём заново
        _LEGACY_CHECKED.add(cid)
        new_lin = os.path.join(THUMB_DIR, cid + ".lin")
        if not os.path.isfile(new_lin):
            old_lin = os.path.join(THUMB_DIR, legacy_thumb_name(p) + ".lin")
            if os.path.isfile(old_lin):
                try:
                    os.replace(old_lin, new_lin)
                    for f in glob.glob(glob.escape(old_lin[:-4]) + "_e*.png"):
                        os.replace(f, os.path.join(THUMB_DIR, cid + os.path.basename(f)[len(os.path.basename(old_lin)) - 4:]))
                except OSError:
                    pass
    return cid


def lin_path(p):
    """Линейная уменьшенная копия HDRI (float32), из неё превью пересчитываются мгновенно."""
    return os.path.join(THUMB_DIR, thumb_name(p) + ".lin")


def png_path(p, ev):
    return os.path.join(THUMB_DIR, "%s_e%+.2f.png" % (thumb_name(p), ev))


def delete_thumbs(p):
    """Удаляет кэш превью (lin + все png) одного файла. Сам HDRI не трогает."""
    n = 0
    base = os.path.join(THUMB_DIR, thumb_name(p))
    for f in [base + ".lin"] + glob.glob(glob.escape(base) + "_e*.png"):
        try:
            os.remove(f)
            n += 1
        except OSError:
            pass
    return n


# ---------- перенос данных из %APPDATA%\SafeEnvironment (версии до v0.73) ----------
_LEGACY_CACHE_ITEMS = ("thumbs", "acg_thumbs", "ph_thumbs", "acg_catalog.json", "ph_catalog.json")
MIGRATION_MARK = os.path.join(DATA_DIR, "migrated_from_SafeEnvironment.txt")


def migrate_legacy_data():
    """Один раз: важные файлы копируются в %APPDATA%\\SkyRigStudio (старая папка остаётся — ей пользуется откат
    на прошлую версию), кэш превью и каталогов переносится в %LOCALAPPDATA%\\SkyRigStudio. Ничего не выбрасывает."""
    try:
        if os.path.isfile(MIGRATION_MARK) or not os.path.isdir(LEGACY_DATA_DIR):
            return
        if os.path.isfile(os.path.join(DATA_DIR, "library.json")):          # новая папка уже используется
            return
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(LOCAL_DIR, exist_ok=True)
        moved, copied = 0, 0
        for name in os.listdir(LEGACY_DATA_DIR):
            src = os.path.join(LEGACY_DATA_DIR, name)
            if name in _LEGACY_CACHE_ITEMS:
                dst = os.path.join(LOCAL_DIR, name)
                if os.path.exists(dst):
                    continue
                try:
                    shutil.move(src, dst)
                    moved += 1
                except Exception:
                    pass
            else:
                dst = os.path.join(DATA_DIR, name)
                if os.path.exists(dst):
                    continue
                try:
                    if os.path.isdir(src):
                        shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)
                    copied += 1
                except Exception:
                    pass
        with open(MIGRATION_MARK, "w", encoding="utf-8") as fh:
            fh.write("Data copied from %s on %s (copied: %d, cache moved: %d).\n"
                     % (LEGACY_DATA_DIR, time.strftime("%Y-%m-%d %H:%M"), copied, moved))
    except Exception:
        pass


migrate_legacy_data()


def rot_to_slider(v):
    """Положение ползунка Rotation (0..360): 0° — левый край, 360° — правый. Значения вне 0..360 сворачиваются."""
    r = int(round(v)) % 360
    return 360 if (r == 0 and v > 0) else r


# Названия тегов освещения (в том числе от старых русских версий) -> названия текущего языка
_TAG_ALIASES = {"утро": "Morning", "полдень": "Noon", "после обеда": "Afternoon", "вечер": "Evening",
                "ночь": "Night", "пасмурное небо": "Overcast", "morning": "Morning", "noon": "Noon",
                "afternoon": "Afternoon", "evening": "Evening", "night": "Night", "overcast": "Overcast"}


def _tag_en(t):
    return _TAG_ALIASES.get(str(t).strip().casefold(), t)


class Library:
    """Группы HDRI: [{"name": str, "files": [path, ...]}]. Файл может быть в нескольких группах."""

    def __init__(self, path=LIB_FILE):
        self.path = path
        d = load_json(path, {})
        self.groups = []
        for g in d.get("groups", []) if isinstance(d, dict) else []:
            if isinstance(g, dict) and isinstance(g.get("name"), str):
                files = [norm_path(f) for f in g.get("files", []) if isinstance(f, str)]
                self.groups.append({"name": g["name"], "files": self._unique(files)})
        self.last_group = d.get("last_group", "") if isinstance(d, dict) else ""
        self.channel_target = d.get("channel_target", "renderer") if isinstance(d, dict) else "renderer"
        if self.channel_target not in ("renderer", "camera"):
            self.channel_target = "renderer"
        self.auto_light = {}                 # path_key -> класс освещения, поставленный автоматически
        raw_al = d.get("auto_light", {}) if isinstance(d, dict) else {}
        if isinstance(raw_al, dict):
            self.auto_light = {k: _tag_en(v) for k, v in raw_al.items() if isinstance(k, str) and _tag_en(v) in LIGHT_CLASSES}
        self.brightness = {}                 # path_key -> {"full": log2 E, "sky": log2 E}
        raw_b = d.get("brightness", {}) if isinstance(d, dict) else {}
        if isinstance(raw_b, dict):
            for k, v in raw_b.items():
                if isinstance(k, str) and isinstance(v, dict):
                    try:
                        self.brightness[k] = {"full": float(v["full"]), "sky": float(v["sky"])}
                    except (KeyError, TypeError, ValueError):
                        pass
        self.norm_ref = d.get("norm_ref", "") if isinstance(d, dict) and isinstance(d.get("norm_ref", ""), str) else ""
        self.norm_enabled = bool(d.get("norm_enabled", False)) if isinstance(d, dict) else False
        self.norm_sun = bool(d.get("norm_sun", False)) if isinstance(d, dict) else False
        self.norm_auto = bool(d.get("norm_auto", False)) if isinstance(d, dict) else False   # поправка при выборе карты
        self.acg_folder = d.get("acg_folder", "") if isinstance(d, dict) and isinstance(d.get("acg_folder", ""), str) else ""
        self.acg_res = d.get("acg_res", "4K") if isinstance(d, dict) and isinstance(d.get("acg_res", "4K"), str) else "4K"
        self.update_auto = bool(d.get("update_auto", True)) if isinstance(d, dict) else True
        self.ui_simple = bool(d.get("ui_simple", False)) if isinstance(d, dict) else False      # режим «Просто»
        uc = d.get("ui_collapsed", []) if isinstance(d, dict) else []
        self.ui_collapsed = [x for x in uc if isinstance(x, str)] if isinstance(uc, list) else []   # свёрнутые секции
        try:
            self.update_checked = float(d.get("update_checked", 0.0)) if isinstance(d, dict) else 0.0
        except (TypeError, ValueError):
            self.update_checked = 0.0
        self.update_latest = d.get("update_latest", "") if isinstance(d, dict) and isinstance(d.get("update_latest", ""), str) else ""
        self.update_notes = d.get("update_notes", "") if isinstance(d, dict) and isinstance(d.get("update_notes", ""), str) else ""
        self.ph_folder = d.get("ph_folder", "") if isinstance(d, dict) and isinstance(d.get("ph_folder", ""), str) else ""
        self.ph_res = d.get("ph_res", "4K") if isinstance(d, dict) and isinstance(d.get("ph_res", "4K"), str) else "4K"
        self.ph_fmt = d.get("ph_fmt", "hdr") if isinstance(d, dict) and d.get("ph_fmt") in ("hdr", "exr") else "hdr"
        try:
            self.acg_size = int(d.get("acg_size", 150)) if isinstance(d, dict) else 150
        except (TypeError, ValueError):
            self.acg_size = 150
        self.tags = {}                       # path_key -> [теги]
        raw = d.get("tags", {}) if isinstance(d, dict) else {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(k, str) and isinstance(v, list):
                    lst = self._clean_tags([_tag_en(t) for t in v if isinstance(t, str)])
                    if lst:
                        self.tags[k] = lst
        try:
            self.thumb_ev = float(d.get("thumb_ev", 0.0)) if isinstance(d, dict) else 0.0
        except (TypeError, ValueError):
            self.thumb_ev = 0.0
        dd = d if isinstance(d, dict) else {}
        self.backup_auto = bool(dd.get("backup_auto", True))               # автобэкап (v0.73)
        self.backup_dir = dd.get("backup_dir", "") if isinstance(dd.get("backup_dir", ""), str) else ""
        try:
            self.backup_keep = max(1, min(100, int(dd.get("backup_keep", BACKUP_KEEP_DEFAULT))))
        except (TypeError, ValueError):
            self.backup_keep = BACKUP_KEEP_DEFAULT
        try:
            self.backup_last = float(dd.get("backup_last", 0.0))
        except (TypeError, ValueError):
            self.backup_last = 0.0
        self.backup_last_hash = dd.get("backup_last_hash", "") if isinstance(dd.get("backup_last_hash", ""), str) else ""
        self.sun_auto_align = bool(dd.get("sun_auto_align", True))         # солнце по HDRI сразу при применении карты
        fav = dd.get("favorites", [])
        self.favorites = self._unique([norm_path(f) for f in fav if isinstance(f, str)]) if isinstance(fav, list) else []

    @staticmethod
    def _unique(paths):
        seen, out = set(), []
        for p in paths:
            k = path_key(p)
            if k not in seen:
                seen.add(k)
                out.append(p)
        return out

    def save(self):
        save_json(self.path, {"version": 2, "groups": self.groups, "last_group": self.last_group,
                              "thumb_ev": self.thumb_ev, "tags": self.tags,
                              "channel_target": self.channel_target,
                              "auto_light": self.auto_light, "brightness": self.brightness,
                              "norm_ref": self.norm_ref, "norm_enabled": self.norm_enabled,
                              "norm_sun": self.norm_sun, "norm_auto": self.norm_auto, "acg_folder": self.acg_folder,
                              "acg_res": self.acg_res, "acg_size": self.acg_size,
                              "ph_folder": self.ph_folder, "ph_res": self.ph_res, "ph_fmt": self.ph_fmt,
                              "update_auto": self.update_auto, "update_checked": self.update_checked,
                              "ui_simple": self.ui_simple, "ui_collapsed": self.ui_collapsed,
                              "update_latest": self.update_latest, "update_notes": self.update_notes,
                              "backup_auto": self.backup_auto, "backup_dir": self.backup_dir,
                              "backup_keep": self.backup_keep, "backup_last": self.backup_last,
                              "backup_last_hash": self.backup_last_hash,
                              "sun_auto_align": self.sun_auto_align, "favorites": self.favorites})

    # ---------- выравнивание яркости ----------
    def norm_offset(self, path, ignore_enabled=False):
        """Поправка экспозиции (EV), при которой карта даёт столько же света, сколько эталон.
        None — выравнивание выключено или для карты/эталона нет измерения."""
        if not (self.norm_enabled or ignore_enabled) or not self.norm_ref:
            return None
        mode = "full" if self.norm_sun else "sky"
        a = self.brightness.get(path_key(self.norm_ref))
        b = self.brightness.get(path_key(path))
        if not a or not b:
            return None
        return round(a[mode] - b[mode], 2)

    def names(self):
        return [g["name"] for g in self.groups]

    # ---------- теги ----------
    @staticmethod
    def parse_tags(text):
        return Library._clean_tags([t for t in re.split(r"[,;\n]", text or "")])

    @staticmethod
    def _clean_tags(tags):
        out, seen = [], set()
        for t in tags:
            t = " ".join(t.split())
            k = t.casefold()
            if t and k not in seen:
                seen.add(k)
                out.append(t)
        return out

    def tags_of(self, path):
        return list(self.tags.get(path_key(path), []))

    def all_tags(self):
        seen = {}
        for lst in self.tags.values():
            for t in lst:
                seen.setdefault(t.casefold(), t)
        return sorted(seen.values(), key=lambda t: t.casefold())

    def set_auto_light(self, path, cls, to_groups=False):
        """Ставит тег типа освещения (прежний автоматический тег заменяется), при to_groups — и группу."""
        k = path_key(path)
        old = self.auto_light.get(k)
        if old and old != cls:
            self.edit_tags([path], [old], "remove")
            if to_groups:
                self.remove_files(old, [path]) if self._find(old) is not None else None
        self.edit_tags([path], [cls], "add")
        self.auto_light[k] = cls
        if to_groups:
            if self._find(cls) is None:
                self.add_group(cls)
            self.add_files(cls, [path])

    def edit_tags(self, paths, tags, mode):
        """mode: "add" | "replace" | "remove"."""
        tags = self._clean_tags(tags)
        drop = {t.casefold() for t in tags}
        for p in paths:
            k = path_key(p)
            cur = self.tags.get(k, [])
            if mode == "replace":
                new = list(tags)
            elif mode == "remove":
                new = [t for t in cur if t.casefold() not in drop]
            else:
                new = self._clean_tags(cur + tags)
            if new:
                self.tags[k] = new
            else:
                self.tags.pop(k, None)

    # ---------- резервный файл библиотеки ----------
    def export_to(self, path):
        """Сохраняет группы и пути в отдельный .json (превью не входят: они строятся на месте)."""
        by_path = {}
        for g in self.groups:
            for f in g["files"]:
                t = self.tags.get(path_key(f))
                if t:
                    by_path[f] = t
        save_json(path, {"format": BACKUP_FORMAT, "version": 2, "groups": self.groups,
                         "thumb_ev": self.thumb_ev, "tags": by_path})

    @staticmethod
    def read_backup(path):
        """Читает резервный файл -> (группы, {путь: [теги]}). Бросает ValueError, если файл не наш."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception as ex:
            raise ValueError("could not read file: %s" % ex)
        if not isinstance(d, dict) or not isinstance(d.get("groups"), list):
            raise ValueError("this is not a SkyRig Studio library backup")
        groups = []
        for g in d["groups"]:
            if isinstance(g, dict) and isinstance(g.get("name"), str):
                files = [norm_path(f) for f in g.get("files", []) if isinstance(f, str)]
                groups.append({"name": g["name"], "files": Library._unique(files)})
        tags = {}
        raw = d.get("tags", {})
        if isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(k, str) and isinstance(v, list):
                    lst = Library._clean_tags([t for t in v if isinstance(t, str)])
                    if lst:
                        tags[norm_path(k)] = lst
        return groups, tags

    def import_groups(self, groups, replace=False, tags=None):
        """replace=True — библиотека заменяется; иначе группы с тем же именем дополняются.
        Возвращает (групп добавлено, файлов добавлено)."""
        tags = tags or {}
        if replace:
            self.groups = [{"name": g["name"], "files": list(g["files"])} for g in groups]
            self.last_group = ""
            self.tags = {}
            for pth, lst in tags.items():
                self.edit_tags([pth], lst, "add")
            return len(groups), sum(len(g["files"]) for g in groups)
        for pth, lst in tags.items():
            self.edit_tags([pth], lst, "add")
        new_groups = new_files = 0
        for g in groups:
            if g["name"] == ALL_GROUP:
                continue
            if self._find(g["name"]) is None:
                self.groups.append({"name": g["name"], "files": []})
                new_groups += 1
            new_files += self.add_files(g["name"], g["files"])
        return new_groups, new_files

    def _find(self, name):
        for g in self.groups:
            if g["name"] == name:
                return g
        return None

    def unique_name(self, base):
        base = (base or "").strip() or "Group"
        if base in (ALL_GROUP, FAV_GROUP):
            base += " (1)"
        name, i = base, 2
        while self._find(name) is not None or name in (ALL_GROUP, FAV_GROUP):
            name = "%s %d" % (base, i)
            i += 1
        return name

    def add_group(self, name):
        name = self.unique_name(name)
        self.groups.append({"name": name, "files": []})
        return name

    def rename_group(self, old, new):
        new = (new or "").strip()
        g = self._find(old)
        if g is None or not new or new in (ALL_GROUP, FAV_GROUP):
            return False
        if new != old and self._find(new) is not None:
            return False
        g["name"] = new
        if self.last_group == old:
            self.last_group = new
        return True

    def delete_group(self, name):
        g = self._find(name)
        if g is not None:
            self.groups.remove(g)

    # ---------- избранное ----------
    def is_favorite(self, path):
        k = path_key(path)
        return any(path_key(f) == k for f in self.favorites)

    def set_favorite(self, paths, on):
        keys = {path_key(p) for p in paths}
        if on:
            have = {path_key(f) for f in self.favorites}
            for p in paths:
                if path_key(p) not in have:
                    self.favorites.append(norm_path(p))
                    have.add(path_key(p))
        else:
            self.favorites = [f for f in self.favorites if path_key(f) not in keys]

    def files_of(self, name):
        if name == FAV_GROUP:
            inlib = {path_key(f) for g in self.groups for f in g["files"]}
            return [f for f in self.favorites if path_key(f) in inlib]
        if name == ALL_GROUP:
            allf = []
            for g in self.groups:
                allf.extend(g["files"])
            return self._unique(allf)
        g = self._find(name)
        return list(g["files"]) if g else []

    def add_files(self, name, paths):
        g = self._find(name)
        if g is None:
            return 0
        have = {path_key(p) for p in g["files"]}
        added = 0
        for p in paths:
            p = norm_path(p)
            if not p.lower().endswith(HDRI_EXTS):
                continue
            if path_key(p) not in have:
                g["files"].append(p)
                have.add(path_key(p))
                added += 1
        return added

    def remove_files(self, name, paths):
        keys = {path_key(p) for p in paths}
        if name == FAV_GROUP:
            before = len(self.favorites)
            self.set_favorite(paths, False)
            return before - len(self.favorites)
        targets = self.groups if name == ALL_GROUP else [g for g in self.groups if g["name"] == name]
        removed = 0
        for g in targets:
            before = len(g["files"])
            g["files"] = [p for p in g["files"] if path_key(p) not in keys]
            removed += before - len(g["files"])
        inlib = {path_key(f) for g in self.groups for f in g["files"]}
        self.favorites = [f for f in self.favorites if path_key(f) in inlib]   # карта ушла из библиотеки — и из избранного
        return removed


class PresetStore:
    def __init__(self, path=PRESET_FILE):
        self.path = path
        d = load_json(path, {})
        self.presets = d.get("presets", {}) if isinstance(d, dict) else {}
        if not isinstance(self.presets, dict):
            self.presets = {}

    def save(self):
        save_json(self.path, {"version": 1, "presets": self.presets})

    def names(self):
        return sorted(self.presets.keys(), key=lambda s: s.lower())

    # ---------- перенос пресетов в другую сцену / на другой компьютер ----------
    def export_to(self, path, names=None):
        chosen = {n: self.presets[n] for n in (names if names is not None else self.presets) if n in self.presets}
        save_json(path, {"format": PRESETS_FORMAT, "version": 1, "presets": chosen})
        return len(chosen)

    @staticmethod
    def read_backup(path):
        """Читает файл пресетов -> {имя: пресет}. Бросает ValueError, если файл не наш."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception as ex:
            raise ValueError("could not read file: %s" % ex)
        if not isinstance(d, dict) or d.get("format") != PRESETS_FORMAT or not isinstance(d.get("presets"), dict):
            raise ValueError("this is not a SkyRig Studio presets file")
        return {n: v for n, v in d["presets"].items() if isinstance(n, str) and isinstance(v, dict)}

    def import_presets(self, presets, on_conflict="rename"):
        """on_conflict: "replace" | "rename" (добавить с номером) | "skip".
        Возвращает (добавлено, заменено, пропущено)."""
        added = replaced = skipped = 0
        for name, p in presets.items():
            if name in self.presets:
                if on_conflict == "skip":
                    skipped += 1
                    continue
                if on_conflict == "replace":
                    self.presets[name] = p
                    replaced += 1
                    continue
                base, i = name, 2
                while name in self.presets:
                    name = "%s (%d)" % (base, i)
                    i += 1
            self.presets[name] = p
            added += 1
        return added, replaced, skipped


class SunCache:
    """Найденная самая яркая точка HDRI: u, v, пик. Ключ — имя по содержимому файла (как у превью)."""

    def __init__(self, path=CACHE_FILE):
        self.path = path
        d = load_json(path, {})
        self.sun = d.get("sun", {}) if isinstance(d, dict) else {}

    @staticmethod
    def _key(p):
        cid = content_id(p)                           # v0.73: по содержимому — не теряется при переносе/переименовании
        return ("c:" + cid) if cid else file_signature(p)

    def get(self, p):
        k = self._key(p)
        c = self.sun.get(k)
        if c is None:                                 # запись старого формата (путь|дата|размер) -> переносим
            c = self.sun.pop(file_signature(p), None)
            if c is not None:
                self.sun[k] = c
        return c

    def put(self, p, u, v, peak):
        self.sun[self._key(p)] = {"u": u, "v": v, "peak": peak}
        if len(self.sun) > 2000:                      # не растём бесконечно
            for k in list(self.sun.keys())[:500]:
                self.sun.pop(k, None)
        try:
            save_json(self.path, {"version": 1, "sun": self.sun})
        except Exception:
            pass


# ==========================================================================
# 2b. Автобэкап библиотеки и превью (v0.73)
# ==========================================================================
# Папка бэкапов (по умолчанию Документы\SkyRig Studio\Backups):
#   SkyRig_backup_2026-10-21_14-32-05_auto.zip — library.json, presets.json, cache.json и manifest.json
#                                                (какие карты были в библиотеке и как называются их превью);
#   thumbs\<имя>.lin                            — общее хранилище превью для всех бэкапов: каждое превью копируется
#                                                один раз (имя по содержимому файла не меняется), поэтому 10 бэкапов
#                                                занимают почти столько же места, сколько один.
# Бэкап делается только по действию пользователя (создание превью, изменения библиотеки, закрытие окна библиотеки) —
# никаких таймеров и фоновых потоков. PNG в бэкап не входят: они за доли секунды строятся из .lin.
BACKUP_PREFIX = "SkyRig_backup_"
BACKUP_MANIFEST = "manifest.json"
BACKUP_THUMBS = "thumbs"
BACKUP_KEEP_DEFAULT = 10
BACKUP_MIN_INTERVAL = 10 * 60          # автобэкап не чаще, чем раз в 10 минут (кроме бэкапа после создания превью)


def default_backup_dir():
    try:
        docs = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.DocumentsLocation)
    except Exception:
        docs = ""
    if not docs:
        docs = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.normpath(os.path.join(docs, "SkyRig Studio", "Backups"))


def backup_dir(lib):
    d = getattr(lib, "backup_dir", "") or ""
    return d if d else default_backup_dir()


def _library_thumb_map(lib):
    """{путь: имя превью} для карт библиотеки, у которых есть превью (.lin)."""
    out = {}
    for f in lib.files_of(ALL_GROUP):
        if not os.path.isfile(f):
            continue
        cid = thumb_name(f)
        if os.path.isfile(os.path.join(THUMB_DIR, cid + ".lin")):
            out[f] = cid
    return out


def library_state_hash(lib):
    """Отпечаток того, что стоит беречь: группы, теги, измерения, пресеты и набор превью."""
    try:
        presets = load_json(PRESET_FILE, {})
    except Exception:
        presets = {}
    data = {"groups": lib.groups, "tags": lib.tags, "auto_light": lib.auto_light, "brightness": lib.brightness,
            "norm_ref": lib.norm_ref, "presets": presets, "favorites": sorted(getattr(lib, "favorites", []) or []),
            "thumbs": sorted(_library_thumb_map(lib).values())}
    return hashlib.sha1(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def list_backups(lib):
    """[(путь к zip, manifest)] — новые первыми. Битые архивы пропускаются."""
    d = backup_dir(lib)
    out = []
    try:
        names = [n for n in os.listdir(d) if n.startswith(BACKUP_PREFIX) and n.lower().endswith(".zip")]
    except OSError:
        return out
    for n in sorted(names, reverse=True):
        path = os.path.join(d, n)
        try:
            with zipfile.ZipFile(path) as z:
                man = json.loads(z.read(BACKUP_MANIFEST).decode("utf-8"))
            if isinstance(man, dict):
                out.append((path, man))
        except Exception:
            continue
    out.sort(key=lambda it: (float(it[1].get("created", 0) or 0), it[0]), reverse=True)
    return out


def _prune_backups(lib):
    keep = max(1, int(getattr(lib, "backup_keep", BACKUP_KEEP_DEFAULT) or BACKUP_KEEP_DEFAULT))
    items = list_backups(lib)
    for path, _man in items[keep:]:
        try:
            os.remove(path)
        except OSError:
            pass
    used = set()
    for _path, man in items[:keep]:
        used.update((man.get("thumbs") or {}).values())
    tdir = os.path.join(backup_dir(lib), BACKUP_THUMBS)
    try:
        for n in os.listdir(tdir):
            if n.endswith(".lin") and n[:-4] not in used:
                try:
                    os.remove(os.path.join(tdir, n))
                except OSError:
                    pass
    except OSError:
        pass


def make_backup(lib, kind="auto"):
    """Создаёт бэкап. Возвращает путь к zip. Бросает исключение, если папка недоступна."""
    lib.save()                                         # в бэкап попадает то, что сейчас в памяти
    save_thumb_index()
    d = backup_dir(lib)
    tdir = os.path.join(d, BACKUP_THUMBS)
    os.makedirs(tdir, exist_ok=True)
    thumbs = _library_thumb_map(lib)
    for cid in set(thumbs.values()):
        dst = os.path.join(tdir, cid + ".lin")
        if not os.path.isfile(dst):                    # имя по содержимому: если файл уже есть, он тот же самый
            tmp = dst + ".tmp"
            shutil.copyfile(os.path.join(THUMB_DIR, cid + ".lin"), tmp)
            os.replace(tmp, dst)
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    path = os.path.join(d, "%s%s_%s.zip" % (BACKUP_PREFIX, stamp, kind))
    man = {"format": "SkyRigStudioBackup", "version": 1, "app_version": VERSION, "created": time.time(),
           "kind": kind, "maps": len(lib.files_of(ALL_GROUP)), "thumbs": thumbs}
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in (LIB_FILE, PRESET_FILE, CACHE_FILE):
            if os.path.isfile(f):
                z.write(f, os.path.basename(f))
        z.writestr(BACKUP_MANIFEST, json.dumps(man, ensure_ascii=False, indent=1))
    os.replace(tmp, path)
    lib.backup_last = time.time()
    lib.backup_last_hash = library_state_hash(lib)
    lib.save()
    _prune_backups(lib)
    alog("backup %s: %s (%d previews)" % (kind, os.path.basename(path), len(thumbs)))
    return path


def maybe_auto_backup(lib, force=False):
    """Автобэкап, если он включён и есть что беречь. force — не ждать 10 минут (после создания превью).
    Возвращает путь к новому бэкапу или None. Ничего не выбрасывает."""
    try:
        if not getattr(lib, "backup_auto", True) or not lib.groups:
            return None
        if not force and time.time() - float(getattr(lib, "backup_last", 0.0) or 0.0) < BACKUP_MIN_INTERVAL:
            return None
        if library_state_hash(lib) == getattr(lib, "backup_last_hash", ""):
            return None
        return make_backup(lib, "auto")
    except Exception:
        print("[SkyRig Studio] auto-backup error:\n" + traceback.format_exc())
        return None


def missing_thumbs_in_backup(lib, files):
    """Карты из files без превью, для которых превью есть в хранилище бэкапов -> [путь]."""
    tdir = os.path.join(backup_dir(lib), BACKUP_THUMBS)
    if not os.path.isdir(tdir):
        return []
    by_path = {}
    for _path, man in list_backups(lib):               # старое имя по пути — на случай, если файл изменился
        for f, cid in (man.get("thumbs") or {}).items():
            by_path.setdefault(path_key(f), cid)
    out = []
    for f in files:
        if not os.path.isfile(f):
            continue
        cid = thumb_name(f)
        if os.path.isfile(os.path.join(THUMB_DIR, cid + ".lin")):
            continue
        if os.path.isfile(os.path.join(tdir, cid + ".lin")) or \
                os.path.isfile(os.path.join(tdir, by_path.get(path_key(f), "-") + ".lin")):
            out.append(f)
    return out


def restore_missing_thumbs(lib, files=None):
    """Возвращает из хранилища бэкапов превью, которых нет в кэше. Текущая библиотека не меняется.
    Возвращает список карт, у которых превью восстановлено (PNG строятся потом через make_thumb)."""
    files = lib.files_of(ALL_GROUP) if files is None else files
    tdir = os.path.join(backup_dir(lib), BACKUP_THUMBS)
    by_path = {}
    for _path, man in list_backups(lib):
        for f, cid in (man.get("thumbs") or {}).items():
            by_path.setdefault(path_key(f), cid)
    os.makedirs(THUMB_DIR, exist_ok=True)
    done = []
    for f in missing_thumbs_in_backup(lib, files):
        cid = thumb_name(f)
        src = os.path.join(tdir, cid + ".lin")
        if not os.path.isfile(src):
            src = os.path.join(tdir, by_path.get(path_key(f), "-") + ".lin")
        try:
            tmp = os.path.join(THUMB_DIR, cid + ".lin.tmp")
            shutil.copyfile(src, tmp)
            os.replace(tmp, os.path.join(THUMB_DIR, cid + ".lin"))
            done.append(f)
        except OSError:
            continue
    if done:
        alog("restored %d previews from backup" % len(done))
    return done


def restore_everything(lib, backup_path):
    """Полный откат: library.json, presets.json и cache.json из бэкапа (перед этим — бэкап текущего состояния),
    затем недостающие превью. После вызова библиотеку, пресеты и кэш солнца нужно перечитать с диска."""
    with zipfile.ZipFile(backup_path) as z:
        names = set(z.namelist())
        if BACKUP_MANIFEST not in names:
            raise ValueError("this is not a SkyRig Studio backup")
        make_backup(lib, "before-restore")
        keep = {k: getattr(lib, k) for k in ("backup_auto", "backup_dir", "backup_keep")}   # настройки бэкапа — текущие
        for f in (LIB_FILE, PRESET_FILE, CACHE_FILE):
            n = os.path.basename(f)
            if n in names:
                tmp = f + ".tmp"
                with open(tmp, "wb") as fh:
                    fh.write(z.read(n))
                os.replace(tmp, f)
        d = load_json(LIB_FILE, {})
        if isinstance(d, dict):
            d.update(keep)
            d["backup_last"] = time.time()
            d["backup_last_hash"] = ""
            save_json(LIB_FILE, d)
    alog("restored everything from %s" % os.path.basename(backup_path))


# ==========================================================================
# 3. MAXScript-вставки (тяжёлые циклы по пикселям выполняются в Max, не в Python)
# ==========================================================================
def mxs_path(p):
    p = os.path.normpath(p)
    if '"' in p or "\n" in p:
        raise ValueError("invalid characters in path: %r" % p)
    return '@"%s"' % p


# Самая яркая точка: грубый поиск по копии <= 2048 px, затем уточнение центром яркости
# в полном разрешении (диск солнца ~10 px на 8K). Возвращает [u, v, пик в линейных единицах].
FINDSUN_OPEN_MXS = r"""
(
 global SE_bm, SE_small
 SE_bm = openBitMap @SRC@
 if SE_bm == undefined then undefined else (
  local w = SE_bm.width, h = SE_bm.height
  local sw = w, sh = h
  SE_small = SE_bm
  if w > 2048 then (
   sw = 2048
   sh = (h * 2048.0 / w) as integer
   SE_small = bitmap sw sh hdr:true color:black
   copy SE_bm SE_small
  )
  [w, h, sw, sh]
 )
)
"""

# Просмотр полос строк уменьшенной копии: возвращает [лучшая яркость, x, y]
FINDSUN_SCAN_MXS = r"""
(
 local bestL = -1.0, bpx = 0, bpy = 0
 local sw = SE_small.width
 for y = @Y0@ to @Y1@ do (
  local row = getPixels SE_small [0,y] sw
  for x = 1 to sw do (
   local c = row[x]
   local l = c.r*0.2126 + c.g*0.7152 + c.b*0.0722
   if l > bestL then (bestL = l; bpx = x-1; bpy = y)
  )
 )
 [bestL, bpx, bpy]
)
"""

# Одна итерация уточнения (яркостный центроид вокруг пика в полном разрешении): [px, py, max]
FINDSUN_REFINE_MXS = r"""
(
 local w = SE_bm.width, h = SE_bm.height
 local cx = @CX@, cy = @CY@, R = @R@
 local y0 = amax #(0, cy - R), y1 = amin #(h-1, cy + R)
 local rowsData = #()
 local mL = -1.0
 for y = y0 to y1 do (
  local row = getPixels SE_bm [0,y] w
  local vals = #()
  for dx = -R to R do (
   local x = mod (cx + dx + w) w
   local c = row[x+1]
   local l = c.r*0.2126 + c.g*0.7152 + c.b*0.0722
   append vals l
   if l > mL then mL = l
  )
  append rowsData vals
 )
 local T = mL * 0.5
 local sL = 0.0, sX = 0.0, sY = 0.0
 for yi = 1 to rowsData.count do (
  local vals = rowsData[yi]
  local y = y0 + yi - 1
  for di = 1 to vals.count do (
   local l = vals[di]
   if l >= T then (
    sL += l
    sX += (cx + (di - 1 - R)) * l
    sY += y * l
   )
  )
 )
 [(sX / sL) + 0.5, (sY / sL) + 0.5, mL]
)
"""

FINDSUN_CLOSE_MXS = r"""
(
 try (if SE_small != undefined and SE_small != SE_bm do close SE_small) catch ()
 try (if SE_bm != undefined do close SE_bm) catch ()
 SE_bm = undefined
 SE_small = undefined
 gc light:true
 ok
)
"""

# Миниатюра: копия TWxTH, простой тонмаппинг (Рейнхард + гамма), сохранение в BMP (без диалогов).
THUMB_MXS = r"""
(
 fn seThumb src dst tw th = (
  local bm = openBitMap src
  if bm == undefined then return false
  local small = bitmap tw th hdr:true color:black
  copy bm small
  close bm
  local fs = fopen dst "wb"
  if fs == undefined then (close small; return false)
  writeLong fs tw
  writeLong fs th
  for y = 0 to th-1 do (
   local row = getPixels small [0,y] tw
   for x = 1 to tw do (
    local c = row[x]
    writeFloat fs (c.r / 255.0)
    writeFloat fs (c.g / 255.0)
    writeFloat fs (c.b / 255.0)
   )
  )
  fclose fs
  close small
  true
 )
 seThumb @SRC@ @DST@ @TW@ @TH@
)
"""


def find_brightest(path, progress=None):
    """(u, v, peak) или None (None и при отмене). Поиск идёт этапами, чтобы можно было
    показывать прогресс: progress(percent, text) -> False отменяет поиск.
    Временные глобальные переменные Max (SE_bm, SE_small) всегда освобождаются."""
    def report(pc, text):
        if progress is None:
            return True
        return progress(pc, text) is not False

    try:
        if not report(0, "Opening HDRI..."):
            return None
        info = rt.execute(FINDSUN_OPEN_MXS.replace("@SRC@", mxs_path(path)))
        if info is None:
            return None
        w, h, sw, sh = int(info[0]), int(info[1]), int(info[2]), int(info[3])
        if not report(15, "Looking for the brightest point..."):
            return None
        best, bpx, bpy = -1.0, 0, 0
        strip = 64
        y = 0
        while y < sh:
            y1 = min(sh - 1, y + strip - 1)
            r = rt.execute(FINDSUN_SCAN_MXS.replace("@Y0@", str(y)).replace("@Y1@", str(y1)))
            if float(r[0]) > best:
                best, bpx, bpy = float(r[0]), int(r[1]), int(r[2])
            y = y1 + 1
            if not report(15 + int(55 * y / sh), "Looking for the brightest point..."):
                return None
        scx, scy = w / float(sw), h / float(sh)
        cx, cy = int((bpx + 0.5) * scx), int((bpy + 0.5) * scy)
        R = int(max(scx, scy) * 3.0 + 14)
        px = py = 0.0
        peak = 0.0
        for it in range(4):
            if not report(70 + it * 7, "Refining position (%d of 4)..." % (it + 1)):
                return None
            r = rt.execute(FINDSUN_REFINE_MXS.replace("@CX@", str(cx)).replace("@CY@", str(cy))
                           .replace("@R@", str(R)))
            px, py, peak = float(r[0]), float(r[1]), float(r[2])
            cx, cy = int(px), int(py)
        report(100, "Done")
        u = (px / w) % 1.0
        return u, py / h, peak / 255.0
    finally:
        try:
            rt.execute(FINDSUN_CLOSE_MXS)
        except Exception:
            pass


# ==========================================================================
# 3b. Определение типа освещения HDRI (по названию файла и по кэшу превью .lin)
# ==========================================================================
LIGHT_MORNING, LIGHT_NOON, LIGHT_AFTERNOON = "Morning", "Noon", "Afternoon"
LIGHT_EVENING, LIGHT_NIGHT, LIGHT_OVERCAST = "Evening", "Night", "Overcast"
LIGHT_CLASSES = (LIGHT_MORNING, LIGHT_NOON, LIGHT_AFTERNOON, LIGHT_EVENING, LIGHT_NIGHT, LIGHT_OVERCAST)

# Пороги подобраны для HDRI в физическом масштабе (небо днём — единицы…десятки, солнце — тысячи+).
# Если карты нормализованы иначе, меняются здесь.
LIGHT_NIGHT_SKY_MEAN = 0.5       # средняя яркость верхней полусферы ниже -> ночь
LIGHT_SUN_FRAC_MIN = 0.10        # доля энергии вокруг самой яркой точки: ниже -> солнца нет
LIGHT_SUN_RATIO_MIN = 30.0       # пик / среднее верхней полусферы: ниже -> солнца нет
LIGHT_NOON_EL = 60.0             # высота солнца от этого значения -> полдень
LIGHT_AFTERNOON_EL = 35.0        # от этого до LIGHT_NOON_EL -> после обеда
LIGHT_WARM_RB = 1.35             # R/B солнца: теплее -> вечер, холоднее -> утро (на высоте 12-35°)
LIGHT_LOW_WARM_RB = 1.5          # то же для солнца у горизонта (ниже 12°)

_NAME_RULES = (                  # порядок важен: первое совпадение побеждает
    (LIGHT_NIGHT, ("night", "nacht", "noche", "moonlit", "moon", "starry", "stars", "ночь", "ночн")),
    (LIGHT_OVERCAST, ("overcast", "cloudy", "rainy", "foggy", "fog", "mist", "пасмур", "дожд", "туман")),
    (LIGHT_EVENING, ("sunset", "evening", "dusk", "twilight", "закат", "вечер")),
    (LIGHT_MORNING, ("sunrise", "dawn", "morning", "утро", "утрен", "рассвет")),
    (LIGHT_AFTERNOON, ("afternoon", "после обеда", "послеобеден")),
    (LIGHT_NOON, ("noon", "midday", "полдень", "полдн")),
)


def light_from_name(path):
    """Тип освещения по ключевым словам в названии файла или None."""
    name = os.path.splitext(os.path.basename(path))[0].lower().replace("_", " ").replace("-", " ")
    for cls, words in _NAME_RULES:
        for w in words:
            if w in name:
                if cls == LIGHT_OVERCAST and "partly" in name:
                    continue                      # «partly cloudy» — это не сплошная облачность
                if w == "noon" and "afternoon" in name:
                    continue
                return cls
    return None


def read_lin(path):
    """(w, h, array('f') RGB) из кэша превью."""
    with open(lin_path(path), "rb") as f:
        data = f.read()
    w, h = struct.unpack("<ii", data[:8])
    px = array.array("f")
    px.frombytes(data[8:8 + w * h * 12])
    if len(px) != w * h * 3:
        raise ValueError("preview cache is damaged")
    return w, h, px


RANGE_LOW_PCT = 0.005      # «тёмный край» диапазона: 0.5 % самых тёмных (ненулевых) пикселей


_SIZE_CACHE = {}


def image_size(path):
    """(ширина, высота) файла из заголовка, без чтения пикселей; None, если не удалось.
    Radiance .hdr, OpenEXR; остальные форматы — через QImageReader."""
    key = (path, os.path.getmtime(path)) if path and os.path.isfile(path) else None
    if key is None:
        return None
    if key in _SIZE_CACHE:
        return _SIZE_CACHE[key]
    res = None
    try:
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as fh:
            head = fh.read(65536)
        if ext in (".hdr", ".pic", ".rgbe"):
            mm = re.search(rb"[-+]Y\s+(\d+)\s+[-+]X\s+(\d+)", head[:4096])
            if mm:
                res = (int(mm.group(2)), int(mm.group(1)))
        elif head[:4] == b"v/1\x01":
            pos = 8
            while pos < len(head) and head[pos] != 0:
                e = head.index(b"\0", pos)
                name = head[pos:e].decode("latin1")
                e2 = head.index(b"\0", e + 1)
                size = int.from_bytes(head[e2 + 1:e2 + 5], "little")
                data = head[e2 + 5:e2 + 5 + size]
                if name == "dataWindow" and size == 16:
                    x0, y0, x1, y1 = struct.unpack("<4i", data)
                    res = (x1 - x0 + 1, y1 - y0 + 1)
                    break
                pos = e2 + 5 + size
        if res is None:
            sz = QtGui.QImageReader(path).size()
            if sz.isValid():
                res = (sz.width(), sz.height())
    except Exception:
        res = None
    _SIZE_CACHE[key] = res
    return res


def res_label(path):
    """«8k» для 8192 px по ширине, иначе «5000x2500»; "" если размер не определён."""
    sz = image_size(path)
    if not sz:
        return ""
    w, h = sz
    k = w / 1024.0
    if k >= 1 and abs(k - round(k)) <= 0.04 * k:
        return "%dk" % round(k)
    return "%dx%d" % (w, h)


def lin_stats(path):
    """(самая яркая яркость, тёмный край) по кэшу превью .lin или None. Нули не учитываются."""
    w, h, px = read_lin(path)
    vals = []
    for i in range(0, w * h * 3, 3):
        l = px[i] * 0.2126 + px[i + 1] * 0.7152 + px[i + 2] * 0.0722
        if l > 1e-6:
            vals.append(l)
    if not vals:
        return None
    vals.sort()
    return vals[-1], vals[min(len(vals) - 1, int(len(vals) * RANGE_LOW_PCT))]


def range_ev(peak, low):
    if not peak or not low or peak <= 0 or low <= 0:
        return None
    return math.log(peak / low, 2.0)


NORM_SKY_CAP = 20.0        # «только небо»: яркость пикселя ограничивается 20 медианами верхней полусферы
DEFAULT_NORM_REF_NAME = "1714_v5"


def brightness_metrics(path):
    """Сколько света карта даёт сцене: освещённость горизонтальной поверхности (верхняя полусфера,
    вес sin(h)·cos(h) на строку). Возвращает {"full": log2 E со всем, "sky": log2 E без диска солнца}
    или None. По кэшу .lin (256x128), Max не нужен. Точность порядка ±0.3 EV."""
    w, h, px = read_lin(path)
    rows = []
    for y in range(h):
        el = (0.5 - (y + 0.5) / h) * math.pi
        if el > 0:
            rows.append((y, math.sin(el) * math.cos(el)))
    if not rows:
        return None
    lums, wts = [], []
    for y, wt in rows:
        base = y * w * 3
        for x in range(w):
            i = base + x * 3
            lums.append(px[i] * 0.2126 + px[i + 1] * 0.7152 + px[i + 2] * 0.0722)
            wts.append(wt)
    nz = sorted(l for l in lums if l > 1e-9)
    if not nz:
        return None
    cap = nz[len(nz) // 2] * NORM_SKY_CAP
    tw = sum(wts)
    e_full = sum(l * wt for l, wt in zip(lums, wts)) / tw
    e_sky = sum(min(l, cap) * wt for l, wt in zip(lums, wts)) / tw
    if e_full <= 0 or e_sky <= 0:
        return None
    return {"full": math.log(e_full, 2.0), "sky": math.log(e_sky, 2.0)}


def classify_light(w, h, px):
    """По картинке 256x128 (равнопромежуточная проекция) -> (класс, подробности).
    Утро/вечер различаются по цвету света (приблизительно): по одной картинке строго
    отличить их нельзя."""
    rows_w = [math.cos((0.5 - (y + 0.5) / h) * math.pi) for y in range(h)]
    lum = array.array("f", [0.0]) * (w * h)
    tot_e = up_e = up_w = 0.0
    best, bx, by = -1.0, 0, 0
    for y in range(h):
        wy = rows_w[y]
        up = y < h // 2
        base = y * w
        for x in range(w):
            i = (base + x) * 3
            l = px[i] * 0.2126 + px[i + 1] * 0.7152 + px[i + 2] * 0.0722
            lum[base + x] = l
            tot_e += l * wy
            if up:
                up_e += l * wy
                up_w += wy
            if l > best:
                best, bx, by = l, x, y
    sky_mean = up_e / up_w if up_w else 0.0
    # окрестность пика (радиус 3 px): энергия, центр, цвет
    se = sx = sy = sr = sb = 0.0
    for y in range(max(0, by - 3), min(h, by + 4)):
        for x in range(bx - 3, bx + 4):
            xx = x % w
            i = (y * w + xx) * 3
            l = lum[y * w + xx] * rows_w[y]
            se += l
            sx += x * l
            sy += y * l
            sr += px[i] * rows_w[y]
            sb += px[i + 2] * rows_w[y]
    cx, cy = (sx / se, sy / se) if se > 0 else (bx, by)
    sun_el = (0.5 - (cy + 0.5) / h) * 180.0
    sun_frac = se / tot_e if tot_e > 0 else 0.0
    ratio = best / sky_mean if sky_mean > 0 else 0.0
    rb = sr / sb if sb > 1e-9 else 1.0
    info = "sky %.3g, peak/sky %.0f, sun share %.2f, elevation %.0f°, R/B %.2f" % (
        sky_mean, ratio, sun_frac, sun_el, rb)
    if sky_mean < LIGHT_NIGHT_SKY_MEAN:
        return LIGHT_NIGHT, info
    if sun_frac < LIGHT_SUN_FRAC_MIN and ratio < LIGHT_SUN_RATIO_MIN:
        return LIGHT_OVERCAST, info
    if sun_el >= LIGHT_NOON_EL:
        return LIGHT_NOON, info
    if sun_el >= LIGHT_AFTERNOON_EL:
        return LIGHT_AFTERNOON, info
    if sun_el >= 12.0:
        return (LIGHT_EVENING if rb >= LIGHT_WARM_RB else LIGHT_MORNING), info
    return (LIGHT_EVENING if rb >= LIGHT_LOW_WARM_RB else LIGHT_MORNING), info


def detect_light(path, use_name=True):
    """(класс | None, откуда). Создаёт кэш превью (.lin) при необходимости."""
    if use_name:
        c = light_from_name(path)
        if c:
            return c, "from file name"
    if not os.path.isfile(lin_path(path)):
        make_thumb(path, 0.0)
    if not os.path.isfile(lin_path(path)):
        return None, "could not read the file"
    w, h, px = read_lin(path)
    cls, info = classify_light(w, h, px)
    return cls, "from image (%s)" % info


def render_thumb(path, ev):
    """Строит PNG из .lin с экспозицией ev (EV) и гаммой THUMB_GAMMA. Быстро, без Max."""
    lin = lin_path(path)
    with open(lin, "rb") as f:
        data = f.read()
    w, h = struct.unpack("<ii", data[:8])
    px = array.array("f")
    px.frombytes(data[8:8 + w * h * 12])
    if len(px) != w * h * 3:
        raise ValueError("preview cache is damaged")
    n = w * h
    # автоэкспозиция: средняя яркость -> 0.4; затем смещение ползунком
    tot = 0.0
    for i in range(0, n * 3, 3):
        l = px[i] * 0.2126 + px[i + 1] * 0.7152 + px[i + 2] * 0.0722
        tot += l if l < 8.0 else 8.0
    mean = max(tot / n, 1e-4)
    k = (0.4 / mean) * (2.0 ** ev)
    inv_g = 1.0 / THUMB_GAMMA
    out = bytearray(n * 3)
    for i in range(n * 3):
        v = px[i] * k
        v = v / (1.0 + v)
        if inv_g != 1.0:
            v = v ** inv_g
        out[i] = 255 if v >= 1.0 else int(v * 255.0 + 0.5)
    img = QtGui.QImage(bytes(out), w, h, w * 3, QtGui.QImage.Format_RGB888).copy()
    png = png_path(path, ev)
    for old in glob.glob(glob.escape(os.path.join(THUMB_DIR, thumb_name(path))) + "_e*.png"):
        try:
            os.remove(old)
        except OSError:
            pass
    return png if img.save(png, "PNG") else None


def make_thumb(path, ev=0.0):
    """Путь к PNG-превью для экспозиции ev (из кэша или созданный) либо None."""
    png = png_path(path, ev)
    if os.path.isfile(png):
        return png
    if not os.path.isfile(path):
        return None
    os.makedirs(THUMB_DIR, exist_ok=True)
    if not os.path.isfile(lin_path(path)):
        tmp = lin_path(path) + ".tmp"
        code = (THUMB_MXS.replace("@SRC@", mxs_path(path)).replace("@DST@", mxs_path(tmp))
                .replace("@TW@", str(THUMB_W)).replace("@TH@", str(THUMB_H)))
        ok = rt.execute(code)
        try:
            rt.gc(light=True)
        except Exception:
            pass
        if not ok or not os.path.isfile(tmp):
            return None
        os.replace(tmp, lin_path(path))
    return render_thumb(path, ev)


# ==========================================================================
# 4. Работа со сценой. Ссылки на объекты НЕ хранятся — только ищутся.
# ==========================================================================
def _class_name(obj):
    try:
        return str(rt.classOf(obj))
    except Exception:
        return ""


SINGLE_MAP_SOURCE = 1                  # Corona: Scene Environment -> Single map (проверено: bg_source = 1)


def corona_renderer():
    """Текущий рендерер, если это Corona; иначе None."""
    try:
        r = rt.renderers.current
        return r if "Corona" in _class_name(r) else None
    except Exception:
        return None


BLUR_MIN, BLUR_MAX, BLUR_DEFAULT = 0.01, 100.0, 1.0   # Blur у CoronaBitmap (filteringBlur)
CC_NAME = "EnvCC"                      # имена наших узлов: по ним отличаем свою цепочку от чужой
BM_NAME = "EnvHDRI"


CC_CLASSES = ("CoronaColorCorrect", "Color_Correction")   # новая карта и старая (v0.17-0.18)


def cc_input(cc):
    """Карта, подключённая ко входу коррекции."""
    return cc.inputTexmap if _class_name(cc) == "CoronaColorCorrect" else cc.map


def cc_set_input(cc, bm):
    if _class_name(cc) == "CoronaColorCorrect":
        cc.inputTexmap = bm
    else:
        cc.map = bm


def sat_pct_to_cc(pct):
    """Окно: 0..200 %, 100 = норма. CoronaColorCorrect (измерено): множитель насыщенности
    f = (1+s)/(1-s), s in [-1, 1]  ->  s = (f-1)/(f+1)."""
    f = max(0.0, float(pct)) / 100.0
    return (f - 1.0) / (f + 1.0)


def cc_to_sat_pct(s):
    s = min(0.999, max(-1.0, float(s)))
    return 100.0 * (1.0 + s) / (1.0 - s)


def cc_read(cc):
    """(EV, gamma, saturation %) из коррекции любого из двух классов."""
    if _class_name(cc) == "CoronaColorCorrect":
        return float(cc.exposure), float(cc.gamma), cc_to_sat_pct(cc.saturation)
    return gain_percent_to_ev(float(cc.gainRGB)), float(cc.gammaRGB), float(cc.saturation) + 100.0


def cc_write(cc, ev, gamma, sat_pct):
    if _class_name(cc) == "CoronaColorCorrect":
        cc.exposure = float(ev)
        cc.gamma = float(gamma)
        cc.saturation = sat_pct_to_cc(sat_pct)
    else:
        cc.gainRGB = ev_to_gain_percent(ev)
        cc.gammaRGB = float(gamma)
        cc.saturation = float(sat_pct) - 100.0


def _our_chain(m):
    """(коррекция, CoronaBitmap), если карта m создана скриптом (имена EnvCC / EnvHDRI);
    пользовательские карты коррекции -> CoronaBitmap считаются чужими."""
    if m is not None and _class_name(m) in CC_CLASSES and str(m.name) == CC_NAME:
        inner = cc_input(m)
        if inner is not None and _class_name(inner) == "CoronaBitmap" and str(inner.name) == BM_NAME:
            return m, inner
    return None


def get_chain():
    """(Color_Correction | None, CoronaBitmap | None, "other" | None).
    Читает слот Scene Environment -> Single map у Corona. Если он пуст, а в слоте Environment
    самого Max осталась наша цепочка (старые версии скрипта) — берётся она."""
    r = corona_renderer()
    if r is None:
        return None, None, None
    env = r.bg_texmap
    if env is None:
        legacy = _our_chain(rt.environmentMap)
        return (legacy[0], legacy[1], None) if legacy else (None, None, None)
    ours = _our_chain(env)
    if ours:
        return ours[0], ours[1], None
    return None, None, "other"           # любая чужая карта (в т.ч. свой Color Correction / CoronaBitmap)


def set_single_map(m):
    """Кладёт карту в Single map и переключает Scene Environment на «Single map»."""
    r = corona_renderer()
    if r is None:
        raise RuntimeError("Current renderer is not Corona")
    r.bg_texmap = m
    r.bg_source = SINGLE_MAP_SOURCE
    try:
        r.bg_texmapUseMain = True            # галочка использования карты справа от слота
    except Exception:
        pass


def adopt_legacy():
    """Переносит нашу цепочку из слота Environment Max в Single map (старые сцены)."""
    r = corona_renderer()
    if r is None or r.bg_texmap is not None:
        return
    legacy = _our_chain(rt.environmentMap)
    if legacy:
        set_single_map(legacy[0])
        rt.environmentMap = None


def ensure_cc(cc, bm):
    """Гарантирует CoronaColorCorrect над CoronaBitmap в слоте Single map.
    Нашу старую стандартную Color_Correction (v0.17-0.18) заменяет на CoronaColorCorrect
    с теми же значениями."""
    if cc is not None and _class_name(cc) == "Color_Correction":
        vals = cc_read(cc)
        new = rt.CoronaColorCorrect()
        new.name = CC_NAME
        cc_set_input(new, bm)
        cc_write(new, *vals)
        set_single_map(new)
        cc = new
    if cc is None:
        cc = rt.CoronaColorCorrect()
        cc.name = CC_NAME
        cc_set_input(cc, bm)
        set_single_map(cc)
    return cc


# Яркость фона отдельно для камеры / отражений / преломлений: поверх основной цепочки в слотах
# Direct / Reflect / Refract кладётся наш CoronaColorCorrect (вход — EnvCC) со сдвигом Exposure.
# Слоты бывают в двух местах: в общих настройках Corona (Scene Environment -> Overrides)
# и в активной CoronaCam (Environment override). Куда писать — выбирает пользователь.
CHANNELS = (("Direct", "EnvCC_Camera"), ("Reflect", "EnvCC_Reflect"), ("Refract", "EnvCC_Refract"))
TARGET_RENDERER, TARGET_CAMERA = "renderer", "camera"


def active_corona_cam():
    """Активная камера вида, если это CoronaCam; иначе None."""
    try:
        c = rt.viewport.getCamera()
        if c is not None and _class_name(c) == "CoronaCam":
            return c
    except Exception:
        pass
    return None


def active_camera_name():
    """Имя камеры активного вида (любой камеры, не только CoronaCam); пустая строка, если вид без камеры."""
    try:
        c = rt.viewport.getCamera()
        if c is not None:
            return str(c.name)
    except Exception:
        pass
    return ""


def _target_obj(kind):
    return corona_renderer() if kind == TARGET_RENDERER else active_corona_cam()


def _ch_attrs(kind, ch):
    """(флаг override, карта, галочка использования карты)."""
    if kind == TARGET_RENDERER:
        return "bg_override" + ch, "bg_texmap" + ch, "bg_texmapUse" + ch
    low = ch.lower()
    return low + "Override", low + "OverrideTexmap", low + "OverrideTexmapUse"


def _our_cc(m, name):
    return m if (m is not None and _class_name(m) == "CoronaColorCorrect" and str(m.name) == name) else None


def channel_ev(kind, ch, name):
    t = _target_obj(kind)
    if t is None:
        return 0.0
    f_on, f_map, _f_use = _ch_attrs(kind, ch)
    m = _our_cc(getattr(t, f_map), name)
    if m is not None and bool(getattr(t, f_on)):
        return float(m.exposure)
    return 0.0


def set_channel_ev(kind, ch, name, ev, main_cc):
    """True — готово; False — в канале чужая карта (не тронута); None — нет цели (нет активной CoronaCam)."""
    t = _target_obj(kind)
    if t is None:
        return None if abs(ev) >= 1e-6 else True
    f_on, f_map, f_use = _ch_attrs(kind, ch)
    cur = getattr(t, f_map)
    ours = _our_cc(cur, name)
    foreign = cur is not None and ours is None and bool(getattr(t, f_on))
    if abs(ev) < 1e-6:
        if ours is not None:
            setattr(t, f_on, False)
            setattr(t, f_map, None)
        return True
    if foreign:
        return False
    cc2 = ours
    if cc2 is None:
        cc2 = rt.CoronaColorCorrect()
        cc2.name = name
    cc2.inputTexmap = main_cc
    cc2.exposure = float(ev)
    setattr(t, f_map, cc2)
    setattr(t, f_on, True)
    try:
        setattr(t, f_use, True)
    except Exception:
        pass
    return True


def clear_channels(keep_kind=None):
    """Убирает НАШИ карты каналов: из общих настроек и из всех CoronaCam (кроме keep_kind)."""
    targets = []
    if keep_kind != TARGET_RENDERER and corona_renderer() is not None:
        targets.append((TARGET_RENDERER, corona_renderer()))
    if keep_kind != TARGET_CAMERA:
        try:
            for c in rt.cameras:
                if _class_name(c) == "CoronaCam":
                    targets.append((TARGET_CAMERA, c))
        except Exception:
            pass
    for kind, t in targets:
        for ch, name in CHANNELS:
            f_on, f_map, _u = _ch_attrs(kind, ch)
            try:
                if _our_cc(getattr(t, f_map), name) is not None:
                    setattr(t, f_on, False)
                    setattr(t, f_map, None)
            except Exception:
                pass


def find_sun():
    """Солнце, которым управляем: EnvSun, иначе первое CoronaSun в сцене."""
    first = None
    for l in rt.lights:
        if _class_name(l) == "CoronaSun":
            if str(l.name) == SUN_NAME:
                return l
            if first is None:
                first = l
    return first


def m_to_units(m):
    """Метры -> единицы сцены (сантиметры, дюймы и т. д. — как настроено в Max)."""
    try:
        return float(rt.units.decodeValue("%.6fm" % float(m)))
    except Exception:
        return float(m) * 100.0


def units_to_m(u):
    k = m_to_units(1.0)
    return float(u) / k if k else float(u)


def _sun_distance(node):
    """Текущее расстояние солнца до центра; если солнце в нуле — значение по умолчанию."""
    p = node.pos
    d = math.sqrt(float(p.x) ** 2 + float(p.y) ** 2 + float(p.z) ** 2)
    return d if d > 1.0 else m_to_units(SUN_DIST_DEFAULT)


def ensure_target(node):
    """Включает Target у солнца и держит цель строго в (0, 0, 0)."""
    if not bool(node.targeted):
        node.targeted = True
    t = node.target
    if t is not None:
        t.pos = rt.point3(0, 0, 0)


def set_sun_direction(node, elevation, azimuth, distance=None):
    """Солнце на сфере вокруг цели (0,0,0): направление на солнце = (elev, azim)."""
    d = float(distance) if distance else _sun_distance(node)
    ensure_target(node)
    x, y, z = sun_basis(elevation, azimuth)
    node.pos = rt.point3(z[0] * d, z[1] * d, z[2] * d)
    node.target.pos = rt.point3(0, 0, 0)


def find_icon():
    for o in rt.objects:
        if str(o.name) == ICON_NAME and _class_name(o) == "Point":
            return o
    return None


def ensure_icon(sun, size):
    """Крупный значок (бокс + оси) рядом с CoronaSun, потому что собственная иконка
    у CoronaSun крошечная и от масштаба не зависит. Значок — потомок солнца."""
    ic = find_icon()
    if ic is None:
        ic = rt.Point(name=ICON_NAME, box=True, cross=False, axistripod=True, centermarker=False,
                      constantscreensize=False, drawontop=False)
        ic.wirecolor = rt.color(255, 229, 0)
        ic.transform = sun.transform
        ic.parent = sun
    ic.size = float(size)
    return ic


# ---------- линия горизонта в камере (v0.73) ----------
# У всех камер Max есть своя линия горизонта в виде из камеры: у Corona Camera — showHorizonLine,
# у Physical — horizon_on, у Target/Free Camera — showHorizon. Она рисуется поверх геометрии и сама идёт за камерой,
# поэтому скрипт ничего не создаёт в сцене — только включает это свойство у камеры текущего вида.
_HLINE_PROPS = ("showHorizonLine", "horizon_on", "showHorizon")


def cam_hline_prop(cam):
    if cam is None:
        return None
    for n in _HLINE_PROPS:
        try:
            if rt.isProperty(cam, n):
                return n
        except Exception:
            pass
    return None


def get_cam_hline(cam):
    """True/False — включена ли линия горизонта у камеры; None — у камеры такого свойства нет."""
    n = cam_hline_prop(cam)
    if n is None:
        return None
    try:
        return bool(rt.getProperty(cam, n))
    except Exception:
        return None


def set_cam_hline(cam, on):
    n = cam_hline_prop(cam)
    if n is None:
        return False
    rt.setProperty(cam, n, bool(on))
    return True


def view_camera():
    try:
        return rt.viewport.getCamera()
    except Exception:
        return None


# ==========================================================================
# 5. Виджеты
# ==========================================================================

# ---------------------------------------------------------------------------------------------
# Оформление (v0.52): единая палитра, карточки, значки. Только внешний вид — на логику не влияет.
# Стили применяются к окну скрипта, а не ко всему 3ds Max; любая ошибка оформления игнорируется.
# ---------------------------------------------------------------------------------------------
UI_ACCENT = "#e0902f"          # тёплый «солнечный» акцент
UI_ACCENT_HOVER = "#eda24a"
UI_CARD_BG = "#3a3a3a"
UI_CARD_BORDER = "#4d4d4d"
UI_BTN_BG = "#4a4a4a"
UI_BTN_HOVER = "#585858"
UI_BTN_PRESSED = "#3a3a3a"

UI_QSS = """
QGroupBox {
    background-color: %(card)s; border: 1px solid %(border)s; border-radius: 6px;
    margin-top: 14px; padding: 8px 6px 6px 6px;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left; left: 8px; padding: 0 4px;
    color: %(accent)s; font-weight: bold;
}
QPushButton {
    background-color: %(btn)s; border: 1px solid %(border)s; border-radius: 4px; padding: 4px 8px; min-height: 16px;
}
QPushButton:hover { background-color: %(hover)s; }
QPushButton:pressed { background-color: %(pressed)s; }
QPushButton:disabled { color: #888888; background-color: #404040; }
QPushButton[primary="true"] {
    background-color: %(accent)s; border: 1px solid %(accent)s; color: #1e1e1e; font-weight: bold;
}
QPushButton[primary="true"]:hover { background-color: %(accent_hover)s; }
QPushButton[primary="true"]:pressed { background-color: #c07a22; }
QPushButton[flat_link="true"] { background-color: transparent; border: none; color: #a8a8a8; }
QPushButton[flat_link="true"]:hover { color: %(accent)s; }
QFrame#card {
    background-color: %(card)s; border: 1px solid %(border)s; border-radius: 6px;
}
QPushButton[cardhead="true"] {
    background-color: transparent; border: none; text-align: left; padding: 3px 2px;
    color: %(accent)s; font-weight: bold;
}
QPushButton[cardhead="true"]:hover { color: %(accent_hover)s; }
QPushButton[seg="true"] { border-radius: 0px; }
QPushButton[seg="true"]:checked { background-color: %(accent)s; color: #1e1e1e; font-weight: bold; border: 1px solid %(accent)s; }
QSlider::groove:horizontal { height: 4px; background: #555555; border-radius: 2px; }
QSlider::sub-page:horizontal { background: %(accent)s; border-radius: 2px; }
QSlider::handle:horizontal {
    background: #e8e8e8; width: 12px; height: 12px; margin: -5px 0; border-radius: 6px;
}
QSlider::handle:horizontal:hover { background: #ffffff; }
QProgressBar { border: 1px solid %(border)s; border-radius: 4px; background: #2e2e2e; text-align: center; }
QProgressBar::chunk { background-color: %(accent)s; border-radius: 3px; }
""" % dict(card=UI_CARD_BG, border=UI_CARD_BORDER, accent=UI_ACCENT, accent_hover=UI_ACCENT_HOVER,
           btn=UI_BTN_BG, hover=UI_BTN_HOVER, pressed=UI_BTN_PRESSED)


def make_icon(name, color="#e8e8e8", size=64):
    """Простые векторные значки, нарисованные QPainter (без внешних файлов и модулей). None при любой ошибке."""
    try:
        pm = QtGui.QPixmap(size, size)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        pen = QtGui.QPen(QtGui.QColor(color))
        pen.setWidthF(size * 0.085)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        p.setPen(pen)
        k = size / 64.0
        P = lambda x, y: QtCore.QPointF(x * k, y * k)
        fill = QtGui.QColor(color)

        def poly(pts, filled=False):
            p.setBrush(fill if filled else QtCore.Qt.NoBrush)
            p.drawPolygon(QtGui.QPolygonF([P(x, y) for x, y in pts]))

        def arc(cx, cy, r, start, span):
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawArc(QtCore.QRectF((cx - r) * k, (cy - r) * k, 2 * r * k, 2 * r * k), int(start * 16), int(span * 16))

        if name == "play":
            poly([(20, 12), (54, 32), (20, 52)], True)
        elif name == "prev":
            poly([(46, 14), (18, 32), (46, 50)], True)
        elif name == "next":
            poly([(18, 14), (46, 32), (18, 50)], True)
        elif name == "library":
            for x, y in ((10, 10), (36, 10), (10, 36), (36, 36)):
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawRoundedRect(QtCore.QRectF(x * k, y * k, 18 * k, 18 * k), 3 * k, 3 * k)
        elif name == "reload":
            arc(32, 32, 20, 40, 270)
            poly([(46, 6), (54, 24), (34, 22)], True)
        elif name == "undo":
            arc(34, 38, 18, 0, 180)
            p.drawLine(P(16, 38), P(16, 26))
            poly([(6, 32), (16, 20), (26, 32)], True)
        elif name == "redo":
            arc(30, 38, 18, 0, 180)
            p.drawLine(P(48, 38), P(48, 26))
            poly([(38, 32), (48, 20), (58, 32)], True)
        elif name == "link":
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawRoundedRect(QtCore.QRectF(6 * k, 22 * k, 28 * k, 20 * k), 10 * k, 10 * k)
            p.drawRoundedRect(QtCore.QRectF(30 * k, 22 * k, 28 * k, 20 * k), 10 * k, 10 * k)
        elif name == "sun":
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawEllipse(P(32, 32), 10 * k, 10 * k)
            for a in range(0, 360, 45):
                dx, dy = math.cos(math.radians(a)), math.sin(math.radians(a))
                p.drawLine(P(32 + dx * 18, 32 + dy * 18), P(32 + dx * 26, 32 + dy * 26))
        elif name == "reset":
            arc(32, 32, 20, 60, 300)
            poly([(10, 8), (14, 28), (32, 18)], True)
        elif name == "chev_down":
            p.drawPolyline(QtGui.QPolygonF([P(14, 24), P(32, 42), P(50, 24)]))
        elif name == "chev_right":
            p.drawPolyline(QtGui.QPolygonF([P(24, 14), P(42, 32), P(24, 50)]))
        elif name == "trash":
            p.drawLine(P(12, 16), P(52, 16))
            p.drawLine(P(26, 16), P(26, 10)); p.drawLine(P(26, 10), P(38, 10)); p.drawLine(P(38, 10), P(38, 16))
            poly([(16, 16), (20, 54), (44, 54), (48, 16)])
        p.end()
        return QtGui.QIcon(pm)
    except Exception:
        return None


def set_btn_icon(btn, name, color="#e8e8e8"):
    """Добавляет значок на кнопку; при ошибке кнопка остаётся как есть."""
    try:
        ic = make_icon(name, color)
        if ic is not None:
            btn.setIcon(ic)
            btn.setIconSize(QtCore.QSize(16, 16))
    except Exception:
        pass


class ToggleSwitch(QtWidgets.QCheckBox):
    """Переключатель-тумблер. Обычный QCheckBox (toggled, isChecked, setChecked работают как раньше), только рисуется иначе."""
    W, H = 30, 16

    def sizeHint(self):
        fm = self.fontMetrics()
        tw = fm.horizontalAdvance(self.text()) if self.text() else 0
        return QtCore.QSize(self.W + (8 + tw if tw else 0) + 2, max(self.H + 4, fm.height() + 4))

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        try:
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            on = self.isChecked()
            enabled = self.isEnabled()
            y = (self.height() - self.H) / 2.0
            track = QtCore.QRectF(1, y, self.W, self.H)
            if not enabled:
                col = QtGui.QColor("#4a4a4a")
            elif on:
                col = QtGui.QColor(UI_ACCENT_HOVER if self.underMouse() else UI_ACCENT)
            else:
                col = QtGui.QColor("#6a6a6a" if self.underMouse() else "#5a5a5a")
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(track, self.H / 2.0, self.H / 2.0)
            r = self.H - 4
            x = track.right() - r - 2 if on else track.left() + 2
            p.setBrush(QtGui.QColor("#f2f2f2" if enabled else "#8a8a8a"))
            p.drawEllipse(QtCore.QRectF(x, y + 2, r, r))
            if self.text():
                p.setPen(QtGui.QColor("#e0e0e0" if enabled else "#808080"))
                p.drawText(QtCore.QRectF(self.W + 10, 0, self.width() - self.W - 10, self.height()),
                           QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, self.text())
            p.end()
        except Exception:
            pass

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


class CollapsibleCard(QtWidgets.QFrame):
    """Карточка со сворачиваемым содержимым: заголовок-кнопка + body. Содержимое кладётся в self.body."""
    state_changed = QtCore.Signal(str, bool)

    def __init__(self, title, key, collapsed=False, parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("card")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 6)
        lay.setSpacing(2)
        self.head = QtWidgets.QPushButton(title)
        self.head.setProperty("cardhead", True)
        self.head.setCursor(QtCore.Qt.PointingHandCursor)
        self.head.setToolTip("Collapse / expand section")
        self.head.clicked.connect(self._toggle)
        lay.addWidget(self.head)
        self.body = QtWidgets.QWidget()
        lay.addWidget(self.body)
        # карточка не растягивается по высоте: лишнее место (когда окно выше содержимого) уходит вниз, под карточки
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        self.body.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        self._collapsed = False
        self.set_collapsed(collapsed)

    def is_collapsed(self):
        return self._collapsed

    def set_collapsed(self, c):
        self._collapsed = bool(c)
        self.body.setVisible(not self._collapsed)
        set_btn_icon(self.head, "chev_right" if self._collapsed else "chev_down", UI_ACCENT)

    def _toggle(self):
        self.set_collapsed(not self._collapsed)
        self.state_changed.emit(self.key, self._collapsed)


class TargetCombo(QtWidgets.QComboBox):
    """Список «Каналы EV в»: пункт про камеру подписан именем активной CoronaCam; имя обновляется при раскрытии списка."""
    def __init__(self, refresh, parent=None):
        super().__init__(parent)
        self._refresh = refresh

    def showPopup(self):
        try:
            self._refresh()
        except Exception:
            pass
        super().showPopup()


def mark_primary(btn):
    btn.setProperty("primary", True)


class ClickableLabel(QtWidgets.QLabel):
    clicked = QtCore.Signal()

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class MaxSpinBox(QtWidgets.QDoubleSpinBox):
    """Спиннер как в 3ds Max: зажать стрелки справа и плавно вести мышь вверх (больше) или
    вниз (меньше). Ctrl — быстрее (x10), Alt — точнее (x0.1). Короткий клик по стрелке — один шаг.
    Правый клик по стрелкам — сброс к значению по умолчанию."""

    PIXEL_DIVIDER = 10.0                 # изменение на 1 пиксель = singleStep / PIXEL_DIVIDER
    DRAG_THRESHOLD = 2                   # пикселей до начала «протяжки»

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_value = None
        self.pixel_divider = self.PIXEL_DIVIDER
        self._arrow_press = False
        self._dragging = False
        self._y0 = 0.0
        self._v0 = 0.0

    def _arrow_zone(self, pos):
        """'up' / 'down' / None — где именно нажато в области стрелок."""
        try:
            opt = QtWidgets.QStyleOptionSpinBox()
            self.initStyleOption(opt)
            st = self.style()
            up = st.subControlRect(QtWidgets.QStyle.CC_SpinBox, opt, QtWidgets.QStyle.SC_SpinBoxUp, self)
            dn = st.subControlRect(QtWidgets.QStyle.CC_SpinBox, opt, QtWidgets.QStyle.SC_SpinBoxDown, self)
            pt = pos.toPoint() if hasattr(pos, "toPoint") else pos
            if up.contains(pt):
                return "up"
            if dn.contains(pt):
                return "down"
            return None
        except Exception:
            x = pos.x()
            if x >= self.width() - 22:
                return "up" if pos.y() < self.height() / 2 else "down"
            return None

    def mousePressEvent(self, e):
        zone = self._arrow_zone(e.position())
        if zone is None:
            super().mousePressEvent(e)
            return
        if e.button() == QtCore.Qt.RightButton:
            if self.default_value is not None:
                self.setValue(self.default_value)
            e.accept()
            return
        if e.button() == QtCore.Qt.LeftButton:
            self._arrow_press = True
            self._dragging = False
            self._zone = zone
            self._y0 = e.position().y()
            self._v0 = self.value()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if not self._arrow_press:
            super().mouseMoveEvent(e)
            return
        dy = self._y0 - e.position().y()            # вверх = положительное
        if not self._dragging:
            if abs(dy) < self.DRAG_THRESHOLD:
                e.accept()
                return
            self._dragging = True
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.SizeVerCursor)
        mods = e.modifiers()
        mult = 1.0
        if mods & QtCore.Qt.ControlModifier:
            mult = 10.0
        elif mods & QtCore.Qt.AltModifier:
            mult = 0.1
        per_px = self.singleStep() / self.pixel_divider * mult
        self.setValue(self._v0 + dy * per_px)
        e.accept()

    def mouseReleaseEvent(self, e):
        if not self._arrow_press:
            super().mouseReleaseEvent(e)
            return
        was_drag = self._dragging
        self._arrow_press = False
        self._dragging = False
        if was_drag:
            QtWidgets.QApplication.restoreOverrideCursor()
        elif e.button() == QtCore.Qt.LeftButton:
            self.stepBy(1 if self._zone == "up" else -1)     # обычный клик = один шаг
        e.accept()

    def keyPressEvent(self, e):
        if e.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.interpretText()          # зафиксировать введённое число
            self.selectAll()
            e.accept()                    # не пропускаем Enter дальше (иначе сработает кнопка окна)
            return
        super().keyPressEvent(e)

    def wheelEvent(self, e):
        # колесо не должно случайно менять значение при прокрутке окна
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


def disable_default_buttons(widget):
    """Enter в поле ввода не должен нажимать «кнопку по умолчанию» диалога."""
    for b in widget.findChildren(QtWidgets.QPushButton):
        b.setAutoDefault(False)
        b.setDefault(False)


def make_spin(lo, hi, step, decimals=2, suffix="", default=None, pixel_divider=None):
    s = MaxSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setKeyboardTracking(False)       # применяем по Enter / потере фокуса
    s.setFocusPolicy(QtCore.Qt.StrongFocus)
    s.default_value = default
    if pixel_divider:
        s.pixel_divider = pixel_divider
    if suffix:
        s.setSuffix(suffix)
    return s


# ==========================================================================
# 6. Окно библиотеки
# ==========================================================================
class TagsDialog(QtWidgets.QDialog):
    """Теги для выбранных файлов: добавить / заменить / убрать."""

    def __init__(self, parent, n_files, current_tags, all_tags):
        super().__init__(parent)
        self.setWindowTitle("Tags")
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("Files selected: %d" % n_files))
        self.lbl_cur = QtWidgets.QLabel("Tags of selected: " + (", ".join(current_tags) if current_tags else "—"))
        self.lbl_cur.setWordWrap(True)
        lay.addWidget(self.lbl_cur)
        self.ed = QtWidgets.QLineEdit()
        self.ed.setPlaceholderText("Tags separated by commas, e.g.: sunset, clear sky")
        comp = QtWidgets.QCompleter(all_tags, self.ed)
        comp.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self.ed.setCompleter(comp)
        lay.addWidget(self.ed)
        self.rb_add = QtWidgets.QRadioButton("Add to existing")
        self.rb_rep = QtWidgets.QRadioButton("Replace all tags")
        self.rb_rem = QtWidgets.QRadioButton("Remove these tags")
        self.rb_add.setChecked(True)
        for rb in (self.rb_add, self.rb_rep, self.rb_rem):
            lay.addWidget(rb)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def result_tags(self):
        mode = "add" if self.rb_add.isChecked() else ("replace" if self.rb_rep.isChecked() else "remove")
        return Library.parse_tags(self.ed.text()), mode


class LightDialog(QtWidgets.QDialog):
    """Параметры автоматического определения типа освещения."""

    def __init__(self, parent, n_selected, n_group, group_name, n_all):
        super().__init__(parent)
        self.setWindowTitle("Detect lighting")
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("Types: " + ", ".join(LIGHT_CLASSES)))
        self.rb_sel = QtWidgets.QRadioButton("Selected files (%d)" % n_selected)
        self.rb_grp = QtWidgets.QRadioButton("Current group “%s” (%d)" % (group_name, n_group))
        self.rb_all = QtWidgets.QRadioButton("Whole library (%d)" % n_all)
        (self.rb_sel if n_selected else self.rb_grp).setChecked(True)
        self.rb_sel.setEnabled(n_selected > 0)
        for rb in (self.rb_sel, self.rb_grp, self.rb_all):
            lay.addWidget(rb)
        self.chk_name = QtWidgets.QCheckBox("Use file name (sunset, overcast, morning...)")
        self.chk_name.setChecked(True)
        self.chk_groups = QtWidgets.QCheckBox("Sort into groups with matching names")
        self.chk_groups.setChecked(False)
        lay.addWidget(self.chk_name)
        lay.addWidget(self.chk_groups)
        note = QtWidgets.QLabel("The result is written to tags (and to groups, if checked). Morning and evening "
                                "are told apart by light color, so this is an estimate; wrong tags can be fixed with the "
                                "“Tags...” button. Files without a preview get one created first (this takes time).")
        note.setWordWrap(True)
        lay.addWidget(note)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def scope(self):
        return "sel" if self.rb_sel.isChecked() else ("grp" if self.rb_grp.isChecked() else "all")


# ==========================================================================
# Обновление скрипта через GitHub (манифест version.json + файл скрипта)
# ==========================================================================
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/lamer486/SkyRigStudio/main/version.json"
UPDATE_CFG_FILE = os.path.join(DATA_DIR, "update_url.txt")        # необязательно: свой адрес манифеста (первая строка)
UPDATE_BACKUP_DIR = os.path.join(DATA_DIR, "backups")
UPDATE_BACKUPS_KEEP = 5
UPDATE_CHECK_SECONDS = 24 * 3600
UPDATE_PROJECT_PAGE = "https://github.com/lamer486/SkyRigStudio"
_UPDATED_FROM = globals().get("_UPDATED_FROM")                    # задаётся при перезапуске после обновления


def update_manifest_url():
    """Адрес манифеста: из update_url.txt (если есть), иначе встроенный."""
    try:
        with open(UPDATE_CFG_FILE, encoding="utf-8") as fh:
            line = fh.readline().strip()
        if line:
            return line
    except OSError:
        pass
    return UPDATE_MANIFEST_URL


def ver_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))) or (0,)


def script_path():
    """Файл, из которого запущен скрипт; None, если определить не удалось (тогда обновление недоступно)."""
    p = globals().get("__file__")
    if p and str(p).lower().endswith(".py") and os.path.isfile(p):
        return os.path.abspath(p)
    try:
        cand = os.path.join(str(rt.getDir(rt.Name("userScripts"))), "SkyRigStudio", "skyrig_studio.py")
        if not os.path.isfile(cand):                                  # установка старой версией (SafeEnvironment)
            cand = os.path.join(str(rt.getDir(rt.Name("userScripts"))), "SafeEnvironment", "safe_environment.py")
        if os.path.isfile(cand):
            return cand
    except Exception:
        pass
    return None


def update_fetch_manifest(timeout=6):
    """Манифест с сайта: {"version", "url", "sha256", "notes"}. Исключение — нет связи или манифест неверный."""
    murl = update_manifest_url()
    if "/OWNER/" in murl:
        raise RuntimeError("update address is not configured yet (the script still has the OWNER placeholder)")
    with acg_open(murl, timeout) as r:
        d = json.loads(r.read().decode("utf-8-sig"))
    ver = str(d["version"]).strip()
    if not re.fullmatch(r"\d+(\.\d+){1,3}", ver):
        raise ValueError("invalid version number in manifest: %r" % ver)
    url = urllib.parse.urljoin(murl, str(d.get("file") or "skyrig_studio.py"))
    if urllib.parse.urlparse(url).scheme != urllib.parse.urlparse(murl).scheme:
        raise ValueError("the update file must be served over the same protocol as the manifest")
    sha = str(d.get("sha256", "")).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("manifest has no sha256 checksum")
    notes = d.get("notes", "")
    if isinstance(notes, list):
        notes = "\n".join("• " + str(x) for x in notes)
    return {"version": ver, "url": url, "sha256": sha, "notes": str(notes)}


def update_download(man, timeout=30):
    """Скачивает файл и проверяет: контрольная сумма, синтаксис Python, версия внутри файла = версия манифеста."""
    with acg_open(man["url"], timeout) as r:
        data = r.read()
    if hashlib.sha256(data).hexdigest() != man["sha256"]:
        raise ValueError("checksum mismatch: the file is damaged or has been tampered with")
    text = data.decode("utf-8-sig")
    compile(text, "safe_environment_update.py", "exec")
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M)
    if not m or m.group(1) != man["version"]:
        raise ValueError("version inside the file (%s) does not match the manifest (%s)" % (m.group(1) if m else "?", man["version"]))
    return data


def update_backup(path):
    """Копия текущего файла в backups/safe_environment_v<версия>.py; хранятся последние UPDATE_BACKUPS_KEEP."""
    os.makedirs(UPDATE_BACKUP_DIR, exist_ok=True)
    cur = VERSION
    try:
        with open(path, encoding="utf-8-sig") as fh:
            m = re.search(r'^VERSION\s*=\s*"([^"]+)"', fh.read(), re.M)
        if m:
            cur = m.group(1)
    except OSError:
        pass
    dst = os.path.join(UPDATE_BACKUP_DIR, "safe_environment_v%s.py" % cur)
    shutil.copyfile(path, dst)
    files = update_backups()
    for _v, p in files[UPDATE_BACKUPS_KEEP:]:
        try:
            os.remove(p)
        except OSError:
            pass
    return dst


def update_backups():
    """[(версия, путь)] — от новых к старым."""
    out = []
    try:
        for n in os.listdir(UPDATE_BACKUP_DIR):
            m = re.fullmatch(r"safe_environment_v([\d.]+)\.py", n)
            if m:
                out.append((m.group(1), os.path.join(UPDATE_BACKUP_DIR, n)))
    except OSError:
        pass
    out.sort(key=lambda x: ver_tuple(x[0]), reverse=True)
    return out


def update_write(path, data):
    """Заменяет файл скрипта (через временный файл рядом: запись целиком либо ничего)."""
    tmp = path + ".new"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def reload_script(path, old_version):
    """Запускает файл заново в чистом пространстве имён: старое окно заменяется новым (как «Run Script»)."""
    import types
    name = "safe_environment_live"
    mod = types.ModuleType(name)
    mod.__file__ = path
    mod._UPDATED_FROM = old_version
    sys.modules[name] = mod
    with open(path, encoding="utf-8-sig") as fh:
        src = fh.read()
    exec(compile(src, path, "exec"), mod.__dict__)


# ==========================================================================
# ambientCG: поиск и загрузка бесплатных HDRI (лицензия CC0)
# ==========================================================================
ACG_SITE = "ambientCG"
ACG_API = "https://ambientcg.com/api/v3/assets"
ACG_UA = "SkyRigStudio/%s (3ds Max script)" % VERSION
ACG_RES_ORDER = ["1K", "2K", "4K", "8K", "12K", "16K"]
ACG_THUMB_DIR = os.path.join(LOCAL_DIR, "acg_thumbs")
ACG_THUMB_KEY = "256-JPG-242424"
ACG_SORTS = (("Popular", "popular"), ("Newest", "latest"),
             ("Most downloads", "downloads"), ("Alphabetical", "alphabet"))


def acg_open(url, timeout=25):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": ACG_UA}), timeout=timeout)


ACG_CATALOG_FILE = os.path.join(LOCAL_DIR, "acg_catalog.json")
ACG_LIGHT_RULES = (                      # тип освещения по названию и тегам сайта (приблизительно)
    (LIGHT_MORNING, ("morning", "sunrise", "dawn")),
    (LIGHT_AFTERNOON, ("afternoon",)),
    (LIGHT_EVENING, ("evening", "sunset", "dusk")),
    (LIGHT_NIGHT, ("night", "stars", "moon")),
    (LIGHT_OVERCAST, ("overcast", "cloudy", "storm", "rain", "fog", "foggy")),
)
ACG_CATS = ("All", "Sky", "Environment", "Interior")


def acg_light_set(a):
    """Множество типов освещения карты по названию и тегам сайта."""
    words = set(t.lower() for t in a["tags"]) | set(a["title"].lower().split())
    out = set()
    for cls, keys in ACG_LIGHT_RULES:
        if any(k in words for k in keys):
            out.add(cls)
    if "noon" in words:
        out.add(LIGHT_NOON)
    if "day" in words and not (out & {LIGHT_AFTERNOON, LIGHT_EVENING, LIGHT_NIGHT, LIGHT_MORNING, LIGHT_OVERCAST}):
        out.add(LIGHT_NOON)
    return out


def acg_prepare(a):
    """Служебные поля для быстрого поиска и фильтров."""
    t = a["title"].lower()
    a["_cat"] = "Interior" if "indoor" in t else ("Sky" if "sky" in t else "Environment")
    a["_light"] = acg_light_set(a)
    a["_hay"] = (a["title"] + " " + a["id"] + " " + " ".join(a["tags"])).casefold()
    return a


def acg_parse(items):
    out = []
    for a in items:
        downs = {}
        for x in a.get("downloads", []):
            if x.get("extension") == "zip" and x.get("attributes") and x.get("url"):
                downs[str(x["attributes"]).upper()] = (str(x["url"]), int(x.get("size", 0) or 0))
        if downs and a.get("id"):
            st = a.get("downloadStatistics") or {}
            out.append({"id": str(a["id"]), "title": str(a.get("title") or a["id"]),
                        "tags": [str(t) for t in a.get("tags", [])],
                        "thumb": str((a.get("thumbnails") or {}).get(ACG_THUMB_KEY, "")), "downloads": downs,
                        "date": str(a.get("releaseDate") or ""), "pop": int(st.get("total", 0) or 0)})
    return out


def acg_fetch_catalog():
    """Весь каталог HDRI одним-двумя запросами (до 500 карт за запрос). Список карт."""
    out, offset = [], 0
    while True:
        params = {"type": "hdri", "sort": "popular", "limit": 500, "offset": offset,
                  "include": "title,tags,thumbnails,downloads,releaseDate,downloadStatistics"}
        with acg_open(ACG_API + "?" + urllib.parse.urlencode(params), 40) as r:
            d = json.loads(r.read().decode("utf-8"))
        got = d.get("assets", [])
        out.extend(acg_parse(got))
        offset += 500
        if not got or offset >= int(d.get("totalResults", 0) or 0):
            break
    return out


def acg_load_cached_catalog():
    """(список карт, время загрузки) из файла или ([], 0)."""
    d = load_json(ACG_CATALOG_FILE, {})
    items = []
    if isinstance(d, dict) and isinstance(d.get("assets"), list):
        for a in d["assets"]:
            try:
                downs = {k: (v[0], int(v[1])) for k, v in a["downloads"].items()}
                items.append({"id": a["id"], "title": a["title"], "tags": list(a["tags"]), "thumb": a["thumb"],
                              "downloads": downs, "date": a.get("date", ""), "pop": int(a.get("pop", 0))})
            except (KeyError, TypeError, ValueError, IndexError):
                continue
        return items, float(d.get("fetched", 0) or 0)
    return [], 0.0


def acg_save_catalog(items):
    save_json(ACG_CATALOG_FILE, {"version": 1, "fetched": time.time(), "assets": items})


def acg_thumb_bytes(asset, key=None):
    """Миниатюра (JPG) из кэша или с сайта. Вызывается и из рабочих потоков: Qt/Max тут не трогаются."""
    url = asset.get("thumb") or ""
    if key:
        url = url.replace(ACG_THUMB_KEY, key)
    if not url:
        return None
    cache = os.path.join(ACG_THUMB_DIR, "%s_%s.jpg" % (asset["id"], key or ACG_THUMB_KEY))
    try:
        if os.path.isfile(cache):
            with open(cache, "rb") as f:
                return f.read()
    except OSError:
        pass
    with acg_open(url, 20) as r:
        data = r.read()
    try:
        os.makedirs(ACG_THUMB_DIR, exist_ok=True)
        with open(cache, "wb") as f:
            f.write(data)
    except OSError:
        pass
    return data


def acg_download(url, dest, progress=None):
    """Скачивает файл блоками. progress(done, total) -> False прерывает. True — файл скачан целиком."""
    with acg_open(url, 30) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(262144)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress is not None and progress(done, total) is False:
                return False
    return True


def acg_extract_exr(zip_path, folder):
    """Достаёт из архива только HDR-карту (.exr) в folder. Путь файла или None."""
    with zipfile.ZipFile(zip_path) as z:
        cands = [i for i in z.infolist() if not i.is_dir() and i.filename.lower().endswith(".exr")]
        pick = [i for i in cands if i.filename.upper().endswith("_HDR.EXR")] or cands
        if not pick:
            return None
        dst = os.path.join(folder, os.path.basename(pick[0].filename))      # только имя: путь из архива не используем
        tmp = dst + ".part"
        with z.open(pick[0]) as src, open(tmp, "wb") as out:
            shutil.copyfileobj(src, out, 1 << 20)
    with open(tmp, "rb") as f:
        magic = f.read(4)
    if magic != b"\x76\x2f\x31\x01":                  # заголовок OpenEXR
        os.remove(tmp)
        return None
    os.replace(tmp, dst)
    return dst


def acg_pick_res(downloads, wanted):
    """Разрешение для карты: нужное, иначе ближайшее меньшее, иначе наименьшее из доступных."""
    if wanted in downloads:
        return wanted
    order = [r for r in ACG_RES_ORDER if r in downloads]
    if not order:
        return None
    lower = [r for r in order if ACG_RES_ORDER.index(r) < ACG_RES_ORDER.index(wanted)] \
        if wanted in ACG_RES_ORDER else []
    return lower[-1] if lower else order[0]


def fmt_mb(n):
    return "%.1f MB" % (n / 1048576.0) if n < 1024 * 1048576 else "%.2f GB" % (n / 1073741824.0)


class AmbientCGDialog(QtWidgets.QDialog):
    """Каталог ambientCG (CC0): весь каталог в одном списке с мгновенным поиском и фильтрами, выбор по превью,
    разрешение, папка; скачанные карты попадают в группу «ambientCG». Подкласс переопределяет источник
    (каталог, превью, загрузку файла) — так сделан и Poly Haven."""

    SITE = ACG_SITE
    TITLE = "Download HDRIs from ambientCG"
    INFO = "CC0 license: free to use, including in commercial projects. Source: ambientcg.com"
    CATS = ACG_CATS
    FOLDER_ATTR, RES_ATTR = "acg_folder", "acg_res"

    # --- источник данных (для ambientCG) ---
    def _fetch(self):
        return acg_fetch_catalog()

    def _cached(self):
        return acg_load_cached_catalog()

    def _save_cat(self, items):
        acg_save_catalog(items)

    def _prepare(self, a):
        return acg_prepare(a)

    def _thumb(self, a, big=False):
        return acg_thumb_bytes(a, "512-JPG-242424" if big else None)

    def _levels(self, a):
        return [r for r in ACG_RES_ORDER if r in a["downloads"]]

    def _avail_res(self, pool):
        return [r for r in ACG_RES_ORDER if any(r in a["downloads"] for a in pool)]

    def _pick(self, downloads, wanted):
        return acg_pick_res(downloads, wanted)

    def _existing(self, a, res, folder):
        return os.path.join(folder, "%s_%s_HDR.exr" % (a["id"], res))

    def _get_file(self, a, res, url, size, folder, tmpdir, prog):
        """Скачивает карту и кладёт в folder. Путь файла, None — ошибка, False — отмена."""
        zpath = os.path.join(tmpdir, "%s_%s.zip" % (a["id"], res))
        try:
            if not acg_download(url, zpath, prog):
                return False
            self.progress.setFormat("%s %s: unpacking..." % (a["title"], res))
            QtWidgets.QApplication.processEvents()
            return acg_extract_exr(zpath, folder)
        finally:
            try:
                os.remove(zpath)
            except OSError:
                pass

    @property
    def folder(self):
        return getattr(self.lib, self.FOLDER_ATTR)

    @folder.setter
    def folder(self, v):
        setattr(self.lib, self.FOLDER_ATTR, v)

    @property
    def res_pref(self):
        return getattr(self.lib, self.RES_ATTR)

    @res_pref.setter
    def res_pref(self, v):
        setattr(self.lib, self.RES_ATTR, v)

    def __init__(self, parent, library, on_changed=None):
        super().__init__(parent)
        self._plan_failed = []
        self.setWindowTitle(self.TITLE)
        self.resize(1040, 760)
        self.lib = library
        self.on_changed = on_changed
        self._catalog = []
        self._assets = {}
        self._icons = {}                 # id -> QIcon (загруженные миниатюры)
        self._failed = set()
        self._placeholder = None
        self._busy = False
        self._cancel = False
        self._thumb_job = False
        self._have = set()
        self._build()
        disable_default_buttons(self)

    # ---------- интерфейс ----------
    def _build(self):
        lay = QtWidgets.QVBoxLayout(self)
        top = QtWidgets.QHBoxLayout()
        self.ed_q = QtWidgets.QLineEdit()
        self.ed_q.setPlaceholderText("Search by name and tags (in English): sunset, overcast, forest, city...")
        self.ed_q.textChanged.connect(self._refill)
        self.cmb_sort = QtWidgets.QComboBox()
        for title, key in (("Popular", "pop"), ("Newest", "date"), ("Alphabetical", "title")):
            self.cmb_sort.addItem(title, key)
        self.cmb_sort.currentIndexChanged.connect(self._refill)
        top.addWidget(self.ed_q, 1)
        top.addWidget(QtWidgets.QLabel("Sort by"))
        top.addWidget(self.cmb_sort)
        lay.addLayout(top)

        fl = QtWidgets.QHBoxLayout()
        fl.addWidget(QtWidgets.QLabel("Type"))
        self.cmb_cat = QtWidgets.QComboBox()
        for c_ in self.CATS:
            self.cmb_cat.addItem(c_)
        self.cmb_cat.setToolTip("Sky: sky only; Environment: sky with ground, city, forest; Interior: indoor spaces.")
        self.cmb_cat.currentIndexChanged.connect(self._refill)
        fl.addWidget(self.cmb_cat)
        fl.addWidget(QtWidgets.QLabel("Site tag"))
        self.cmb_tag = QtWidgets.QComboBox()
        self.cmb_tag.setMinimumWidth(110)
        self.cmb_tag.currentIndexChanged.connect(self._refill)
        fl.addWidget(self.cmb_tag)
        self.chk_hide = QtWidgets.QCheckBox("Hide already downloaded")
        self.chk_hide.toggled.connect(self._refill)
        fl.addWidget(self.chk_hide)
        fl.addStretch(1)
        fl.addWidget(QtWidgets.QLabel("Thumbnail size"))
        self.sl_size = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sl_size.setRange(80, 230)
        self.sl_size.setValue(max(80, min(230, self.lib.acg_size)))
        self.sl_size.setFixedWidth(130)
        self.sl_size.valueChanged.connect(self._on_size)
        fl.addWidget(self.sl_size)
        self.b_refresh = QtWidgets.QPushButton("Refresh catalog")
        self.b_refresh.setToolTip("Reload the list of maps from the site (new maps will appear).")
        self.b_refresh.clicked.connect(lambda: self._load_catalog(force=True))
        fl.addWidget(self.b_refresh)
        lay.addLayout(fl)

        lr = QtWidgets.QHBoxLayout()
        lr.addWidget(QtWidgets.QLabel("Lighting"))
        self.chips = {}
        for cls in LIGHT_CLASSES:
            b = QtWidgets.QPushButton(cls)
            b.setCheckable(True)
            b.setToolTip("Detected from the title and site tags (approximate). You can select several.")
            b.toggled.connect(self._refill)
            lr.addWidget(b)
            self.chips[cls] = b
        b_clear = QtWidgets.QPushButton("Reset filters")
        b_clear.clicked.connect(self._clear_filters)
        lr.addStretch(1)
        lr.addWidget(b_clear)
        lay.addLayout(lr)

        self.lst = QtWidgets.QListWidget()
        self.lst.setViewMode(QtWidgets.QListView.IconMode)
        self.lst.setResizeMode(QtWidgets.QListView.Adjust)
        self.lst.setMovement(QtWidgets.QListView.Static)
        self.lst.setWordWrap(True)
        self.lst.setUniformItemSizes(True)
        self.lst.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.lst.verticalScrollBar().setSingleStep(24)
        self.lst.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.lst.itemSelectionChanged.connect(self._on_selection)
        self.lst.itemDoubleClicked.connect(self._show_big)
        self.lst.verticalScrollBar().valueChanged.connect(self._load_visible)
        lay.addWidget(self.lst, 1)
        self.lbl_page = QtWidgets.QLabel("")
        self.lbl_page.setAlignment(QtCore.Qt.AlignCenter)
        lay.addWidget(self.lbl_page)
        self._apply_size()

        fr = QtWidgets.QHBoxLayout()
        fr.addWidget(QtWidgets.QLabel("Maps folder:"))
        self.lbl_folder = QtWidgets.QLabel("")
        self.lbl_folder.setStyleSheet("color: #d0d0d0;")
        self.lbl_folder.setWordWrap(True)
        b_folder = QtWidgets.QPushButton("Choose folder...")
        b_folder.clicked.connect(self._choose_folder)
        fr.addWidget(self.lbl_folder, 1)
        fr.addWidget(b_folder)
        lay.addLayout(fr)

        rr = QtWidgets.QHBoxLayout()
        rr.addWidget(QtWidgets.QLabel("Resolution:"))
        self.cmb_res = QtWidgets.QComboBox()
        self.cmb_res.setMinimumWidth(80)
        self.cmb_res.currentIndexChanged.connect(self._on_res)
        rr.addWidget(self.cmb_res)
        self.lbl_sel = QtWidgets.QLabel("Select maps in the list (double-click for a large preview).")
        self.lbl_sel.setWordWrap(True)
        rr.addWidget(self.lbl_sel, 1)
        self.b_dl = QtWidgets.QPushButton("Download selected")
        self.b_dl.setMinimumHeight(30)
        self.b_dl.setEnabled(False)
        self.b_dl.clicked.connect(self._download)
        rr.addWidget(self.b_dl)
        lay.addLayout(rr)

        pr = QtWidgets.QHBoxLayout()
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(True)
        self.progress.setAlignment(QtCore.Qt.AlignCenter)
        self.progress.setFormat("")
        self.b_cancel = QtWidgets.QPushButton("Cancel")
        self.b_cancel.setEnabled(False)
        self.b_cancel.clicked.connect(self._on_cancel)
        b_close = QtWidgets.QPushButton("Close")
        b_close.clicked.connect(self.accept)
        pr.addWidget(self.progress, 1)
        pr.addWidget(self.b_cancel)
        pr.addWidget(b_close)
        lay.addLayout(pr)

        self.lbl_info = QtWidgets.QLabel(self.INFO)
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setStyleSheet("color: #b0b0b0;")
        lay.addWidget(self.lbl_info)
        self._show_folder()

    def _info(self, text, error=False):
        self.lbl_info.setStyleSheet("color: #ff7070;" if error else "color: #d0d0d0;")
        self.lbl_info.setText(text)

    def _on_cancel(self):
        self._cancel = True

    def showEvent(self, e):
        super().showEvent(e)
        if not self._catalog and not self._busy:
            self._load_catalog(force=False)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._load_visible()

    # ---------- папка ----------
    def _show_folder(self):
        f = self.folder
        self.lbl_folder.setText(f if f else "not set (will be asked on download)")

    def _choose_folder(self):
        start = self.folder if self.folder and os.path.isdir(self.folder) \
            else os.path.expanduser("~")
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Folder for downloaded HDRIs", start)
        if not d:
            return False
        self.folder = norm_path(d)
        try:
            self.lib.save()
        except Exception:
            pass
        self._show_folder()
        return True

    # ---------- каталог ----------
    def _load_catalog(self, force):
        if self._busy:
            return
        items, ts = ([], 0.0) if force else self._cached()
        note = ""
        if not items:
            self._busy = True
            self.setCursor(QtCore.Qt.WaitCursor)
            self._info("Loading catalog from %s..." % self.SITE)
            QtWidgets.QApplication.processEvents()
            try:
                items = self._fetch()
                if items:
                    try:
                        self._save_cat(items)
                    except Exception:
                        print("[SkyRig Studio] catalog save error:\n" + traceback.format_exc())
                    ts = time.time()
                    note = "Catalog updated. "
            except Exception as ex:
                old, ts = self._cached()
                if old:
                    items = old
                    note = "No connection to the site (%s), showing the saved catalog. " % ex
                else:
                    self._info("Could not load the catalog from %s: %s. Check your internet connection." % (self.SITE, ex),
                               error=True)
            finally:
                self.unsetCursor()
                self._busy = False
        self._catalog = [self._prepare(a) for a in items]
        self._assets = {a["id"]: a for a in self._catalog}
        self._failed.clear()
        counts = {}
        for a in self._catalog:
            for t in a["tags"]:
                tl = t.lower()
                if tl in ("hdri", "sky", "environment") or tl.isdigit() or (tl[:-1].isdigit() and len(tl) <= 4):
                    continue
                counts[tl] = counts.get(tl, 0) + 1
        cur = self.cmb_tag.currentData()
        self.cmb_tag.blockSignals(True)
        self.cmb_tag.clear()
        self.cmb_tag.addItem("any", "")
        for t, n in sorted(((t, n) for t, n in counts.items() if n >= 6), key=lambda x: -x[1]):
            self.cmb_tag.addItem("%s (%d)" % (t, n), t)
        i = self.cmb_tag.findData(cur or "")
        self.cmb_tag.setCurrentIndex(max(0, i))
        self.cmb_tag.blockSignals(False)
        self._refill()
        if self._catalog:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "—"
            self._info("%sCatalog from %s: %d maps. Pick by preview (Ctrl/Shift for several), then a resolution "
                       "and “Download selected”." % (note, when, len(self._catalog)))

    def _have_ids(self):
        have = set()
        for p in self.lib.files_of(ALL_GROUP):
            m = re.match(r"^(.+?)_\d+K_HDR\.exr$", os.path.basename(p), re.I)
            if m:
                have.add(m.group(1).casefold())
        return have

    def _passes(self, a, words, cat, tag, lights):
        if cat != "All" and a["_cat"] != cat:
            return False
        if tag and tag not in [t.lower() for t in a["tags"]]:
            return False
        if lights and not (a["_light"] & lights):
            return False
        if self.chk_hide.isChecked() and a["id"].casefold() in self._have:
            return False
        return all(w in a["_hay"] for w in words)

    def _refill(self, *_):
        """Перестраивает список по поиску, фильтрам и сортировке (без обращений к сайту)."""
        if not self._catalog:
            return
        self._have = self._have_ids()
        words = self.ed_q.text().casefold().split()
        cat = self.cmb_cat.currentText()
        tag = self.cmb_tag.currentData() or ""
        lights = {c for c, b in self.chips.items() if b.isChecked()}
        key = self.cmb_sort.currentData()
        res = [a for a in self._catalog if self._passes(a, words, cat, tag, lights)]
        if key == "pop":
            res.sort(key=lambda a: -a["pop"])
        elif key == "date":
            res.sort(key=lambda a: a["date"], reverse=True)
        else:
            res.sort(key=lambda a: a["title"].casefold())
        selected = {a["id"] for a in self._selected()}
        self.lst.setUpdatesEnabled(False)
        self.lst.clear()
        for a in res:
            have = a["id"].casefold() in self._have
            it = QtWidgets.QListWidgetItem(("✓ " if have else "") + a["title"])
            it.setData(QtCore.Qt.UserRole, a["id"])
            it.setIcon(self._icons.get(a["id"]) or self._ph_icon())
            levels = self._levels(a)
            it.setToolTip("%s\nTags: %s\nResolutions: %s%s" % (
                a["title"], ", ".join(a["tags"]) or "—", ", ".join(levels), "\nAlready in the library" if have else ""))
            if have:
                it.setForeground(QtGui.QBrush(QtGui.QColor(130, 210, 130)))
            self.lst.addItem(it)
            if a["id"] in selected:
                it.setSelected(True)
        self.lst.setUpdatesEnabled(True)
        self.lbl_page.setText("Maps shown: %d of %d" % (len(res), len(self._catalog)))
        self._on_selection()
        self._load_visible()

    def _clear_filters(self):
        for w in (self.ed_q, self.cmb_cat, self.cmb_tag, self.chk_hide, self.cmb_sort, *self.chips.values()):
            w.blockSignals(True)
        self.ed_q.clear()
        self.cmb_cat.setCurrentIndex(0)
        self.cmb_tag.setCurrentIndex(0)
        self.chk_hide.setChecked(False)
        self.cmb_sort.setCurrentIndex(0)
        for b in self.chips.values():
            b.setChecked(False)
        for w in (self.ed_q, self.cmb_cat, self.cmb_tag, self.chk_hide, self.cmb_sort, *self.chips.values()):
            w.blockSignals(False)
        self._refill()

    # ---------- миниатюры (подгружаются только для видимых карт) ----------
    def _ph_icon(self):
        if self._placeholder is None:
            pm = QtGui.QPixmap(64, 64)
            pm.fill(QtGui.QColor(48, 48, 48))
            self._placeholder = QtGui.QIcon(pm)
        return self._placeholder

    def _apply_size(self):
        sz = self.sl_size.value()
        self.lst.setIconSize(QtCore.QSize(sz, sz))
        self.lst.setGridSize(QtCore.QSize(sz + 26, sz + 44))

    def _on_size(self, v):
        self.lib.acg_size = int(v)
        self._apply_size()
        self._load_visible()

    def _load_visible(self, *_):
        """Скачивает миниатюры карт, попавших в видимую область (и экран запаса сверху/снизу)."""
        if self._thumb_job or not self._catalog:
            return
        vp = self.lst.viewport().rect()
        vp = vp.adjusted(0, -vp.height() // 2, 0, vp.height())
        need = []
        for i in range(self.lst.count()):
            it = self.lst.item(i)
            aid = it.data(QtCore.Qt.UserRole)
            if aid in self._icons or aid in self._failed:
                continue
            if self.lst.visualItemRect(it).intersects(vp):
                need.append(self._assets[aid])
                if len(need) >= 60:
                    break
        if not need:
            return
        self._thumb_job = True
        try:
            def fetch(a):
                try:
                    return self._thumb(a)
                except Exception:
                    return None
            with ThreadPoolExecutor(max_workers=8) as ex:       # только байты из сети; Qt — в основном потоке
                datas = list(ex.map(fetch, need))
            for a, data in zip(need, datas):
                pm = QtGui.QPixmap()
                if data and pm.loadFromData(data):
                    self._icons[a["id"]] = QtGui.QIcon(pm)
                else:
                    self._failed.add(a["id"])
            for i in range(self.lst.count()):
                it = self.lst.item(i)
                ic = self._icons.get(it.data(QtCore.Qt.UserRole))
                if ic is not None:
                    it.setIcon(ic)
        finally:
            self._thumb_job = False

    def _show_big(self, item):
        a = self._assets.get(item.data(QtCore.Qt.UserRole))
        if not a:
            return
        try:
            data = self._thumb(a, True)
        except Exception as ex:
            self._info("Could not load the large preview: %s" % ex, error=True)
            return
        pm = QtGui.QPixmap()
        if not data or not pm.loadFromData(data):
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(a["title"])
        v = QtWidgets.QVBoxLayout(dlg)
        lb = QtWidgets.QLabel()
        lb.setPixmap(pm)
        v.addWidget(lb)
        v.addWidget(QtWidgets.QLabel("Tags: " + (", ".join(a["tags"]) or "—")))
        dlg.exec()

    # ---------- выбор и разрешение ----------
    def _selected(self):
        out = []
        for it in self.lst.selectedItems():
            a = self._assets.get(it.data(QtCore.Qt.UserRole))
            if a:
                out.append(a)
        return out

    def _on_selection(self):
        sel = self._selected()
        pool = sel or self._catalog
        avail = self._avail_res(pool)
        keep = self.cmb_res.currentData() or self.res_pref
        self.cmb_res.blockSignals(True)
        self.cmb_res.clear()
        for r in avail:
            self.cmb_res.addItem(r, r)
        idx = self.cmb_res.findData(keep)
        if idx < 0:
            idx = self.cmb_res.findData("4K")
        self.cmb_res.setCurrentIndex(max(0, idx))
        self.cmb_res.blockSignals(False)
        self._on_res()

    def _on_res(self, *_):
        res = self.cmb_res.currentData()
        sel = self._selected()
        self.b_dl.setEnabled(bool(sel) and not self._busy)
        if not sel:
            self.lbl_sel.setText("Select maps in the list (double-click for a large preview).")
            return
        if not res:
            self.lbl_sel.setText("Maps selected: %d." % len(sel))
            return
        self.res_pref = res
        total, lower, unknown = 0, [], False
        for a in sel:
            use = self._pick(a["downloads"], res)
            if use:
                sz = a["downloads"][use][1]
                total += sz
                unknown = unknown or not sz
                if use != res and res != "MAX":
                    lower.append("%s — %s" % (a["title"], use))
        txt = "Maps selected: %d%s." % (len(sel), ", size will be determined on download" if unknown
                                       else ", about %s" % fmt_mb(total))
        if lower:
            txt += " No %s for: %s." % (res, "; ".join(lower))
        if res in ("12K", "16K", "24K", "MAX"):
            txt += " High resolution: creating the preview will take time and a lot of memory."
        self.lbl_sel.setText(txt)

    # ---------- загрузка ----------
    def _plan(self):
        res = self.cmb_res.currentData()
        plan = []
        for a in self._selected():
            use = self._pick(a["downloads"], res)
            if use:
                url, size = a["downloads"][use]
                plan.append((a, use, url, size))
        return plan

    def _download(self):
        if self._busy:
            return
        self._plan_failed = []
        plan = self._plan()
        if not plan:
            if self._plan_failed:
                self._info("Could not get download links: %s" % ", ".join(self._plan_failed), error=True)
            return
        folder = self.folder
        if not folder or not os.path.isdir(folder):
            if not self._choose_folder():
                return
            folder = self.folder
        total = sum(p[3] for p in plan)
        if total > 400 * 1048576:
            r = QtWidgets.QMessageBox.question(
                self, "Download selected", "In total about %s will be downloaded (maps: %d). Continue?" % (fmt_mb(total), len(plan)))
            if r != QtWidgets.QMessageBox.Yes:
                return
        self._busy, self._cancel = True, False
        self.b_dl.setEnabled(False)
        self.b_cancel.setEnabled(True)
        done_files, failed, skipped = [], list(self._plan_failed), []
        tmpdir = tempfile.mkdtemp(prefix="se_acg_")
        try:
            for n, (a, res, url, size) in enumerate(plan, 1):
                if self._cancel:
                    break
                label = "%s %s (%d of %d)" % (a["title"], res, n, len(plan))
                existing = self._existing(a, res, folder)
                path = None
                if os.path.isfile(existing):
                    path = existing
                    skipped.append(a["title"])
                else:
                    def prog(done, tot, label=label, size=size):
                        t = tot or size or 1
                        self.progress.setValue(int(min(100, done * 100 / t)))
                        self.progress.setFormat("%s: %s of %s" % (label, fmt_mb(done), fmt_mb(t)))
                        QtWidgets.QApplication.processEvents()
                        return not self._cancel

                    try:
                        path = self._get_file(a, res, url, size, folder, tmpdir, prog)
                        if path is False:
                            break
                    except Exception:
                        print("[SkyRig Studio] %s download error for %s:\n%s" % (self.SITE, a["id"], traceback.format_exc()))
                        path = None
                if not path:
                    failed.append(a["title"])
                    continue
                self.progress.setFormat("%s: adding to the library..." % label)
                QtWidgets.QApplication.processEvents()
                self._register(path, res)
                done_files.append(a["title"])
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
            self._busy = False
            self.b_cancel.setEnabled(False)
            self.progress.setValue(100 if not self._cancel else self.progress.value())
            self._on_res()
        try:
            self.lib.save()
        except Exception:
            print("[SkyRig Studio] library save error:\n" + traceback.format_exc())
        msg = "Downloaded and added to group “%s”: %d" % (self.SITE, len(done_files))
        if skipped:
            msg += " (already on disk: %d)" % len(skipped)
        if failed:
            msg += ". Failed: %s" % ", ".join(failed)
        if self._cancel:
            msg += ". Interrupted."
        self.progress.setFormat(msg if len(msg) < 90 else "Done: %d" % len(done_files))
        self._info(msg, error=bool(failed))
        self._refill()                                   # метки «✓ уже скачано»
        if self.on_changed:
            try:
                self.on_changed()
            except Exception:
                pass

    def _register(self, path, res):
        """Группа «ambientCG», теги: тип освещения и разрешение, превью."""
        lib = self.lib
        if lib._find(self.SITE) is None:
            lib.add_group(self.SITE)
        lib.add_files(self.SITE, [path])
        lib.edit_tags([path], [res], "add")
        try:
            cls, _how = detect_light(path, True)
            if cls:
                lib.set_auto_light(path, cls)
        except Exception:
            print("[SkyRig Studio] light detect error for %s:\n%s" % (path, traceback.format_exc()))
        try:
            make_thumb(path, lib.thumb_ev)
        except Exception:
            print("[SkyRig Studio] thumb error for %s:\n%s" % (path, traceback.format_exc()))


# ==========================================================================
# Poly Haven: поиск и загрузка бесплатных HDRI (лицензия CC0)
# ==========================================================================
PH_SITE = "Poly Haven"
PH_API = "https://api.polyhaven.com"
PH_THUMB_DIR = os.path.join(LOCAL_DIR, "ph_thumbs")
PH_CATALOG_FILE = os.path.join(LOCAL_DIR, "ph_catalog.json")
PH_CATS = ("All", "Sky", "Nature", "City", "Interior", "Studio")
PH_STD_K = (1, 2, 4, 8, 16, 24)


def ph_json(path, timeout=40):
    with acg_open(PH_API + path, timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ph_levels(max_w):
    """Уровни разрешения, которые есть у карты (ширина max_w): стандартные 1K..24K и свой максимум (например 29K)."""
    top = int(max_w // 1024)
    ks = [k for k in PH_STD_K if k <= top]
    if top > 1 and top not in ks:
        ks.append(top)
    return ["%dK" % k for k in ks] or ["1K"]


def ph_light_set(attr, cats):
    tod = str((attr or {}).get("time_of_day", "")).lower()
    wea = str((attr or {}).get("weather", "")).lower()
    out = set()
    out |= {"sunrise": {LIGHT_MORNING}, "morning": {LIGHT_MORNING}, "midday": {LIGHT_NOON},
            "afternoon": {LIGHT_AFTERNOON}, "sunset": {LIGHT_EVENING}, "dusk": {LIGHT_EVENING},
            "night": {LIGHT_NIGHT}}.get(tod, set())
    if wea in ("overcast", "fog", "rain", "snow"):
        out.add(LIGHT_OVERCAST)
    return out


def ph_parse(d):
    """Ответ /assets?t=hdris -> список карт в нашем формате (без URL: ссылки берутся при скачивании)."""
    out = []
    for aid, v in (d or {}).items():
        try:
            cats = [str(x) for x in v.get("categories", [])]
            tags = [str(x) for x in v.get("tags", [])]
            allt = []
            for t in cats + tags:
                if t.lower() not in [x.lower() for x in allt] and not t.lower().startswith("collection:"):
                    allt.append(t)
            cl = [x.lower() for x in cats]
            if "studio" in cl:
                cat = "Studio"
            elif "indoor" in cl or (v.get("attributes") or {}).get("environment") == "indoor":
                cat = "Interior"
            elif "pure skies" in cl:
                cat = "Sky"
            elif "urban" in cl:
                cat = "City"
            elif "nature" in cl:
                cat = "Nature"
            else:
                cat = "Environment"
            mr = v.get("max_resolution") or [1024, 512]
            out.append({"id": str(aid), "title": str(v.get("name") or aid), "tags": allt,
                        "levels": ph_levels(int(mr[0])), "date": "%012d" % int(v.get("date_published") or 0),
                        "pop": int(v.get("download_count") or 0), "cat": cat,
                        "light": sorted(ph_light_set(v.get("attributes"), cats))})
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
    return out


def ph_fetch_catalog():
    return ph_parse(ph_json("/assets?t=hdris"))


def ph_from_cache(items):
    return [{"id": a["id"], "title": a["title"], "tags": list(a["tags"]), "downloads": {r: (None, 0) for r in a["levels"]},
             "levels": list(a["levels"]), "date": a["date"], "pop": a["pop"], "_cat": a["cat"],
             "_light": set(a["light"]), "thumb": a["id"]} for a in items]


def ph_load_cached_catalog():
    d = load_json(PH_CATALOG_FILE, {})
    if isinstance(d, dict) and isinstance(d.get("assets"), list):
        try:
            return ph_to_items(d["assets"]), float(d.get("fetched", 0) or 0)
        except (KeyError, TypeError, ValueError):
            pass
    return [], 0.0


def ph_to_items(raw):
    return [{"id": a["id"], "title": a["title"], "tags": list(a["tags"]), "levels": list(a["levels"]), "date": a["date"],
             "pop": int(a["pop"]), "cat": a["cat"], "light": list(a["light"])} for a in raw]


def ph_thumb_bytes(asset, big=False):
    """Превью (PNG) из кэша или с cdn.polyhaven.com. Вызывается и из рабочих потоков: Qt/Max тут не трогаются."""
    h = 512 if big else 128
    cache = os.path.join(PH_THUMB_DIR, "%s_%d.png" % (asset["id"], h))
    try:
        if os.path.isfile(cache):
            with open(cache, "rb") as f:
                return f.read()
    except OSError:
        pass
    url = "https://cdn.polyhaven.com/asset_img/primary/%s.png?height=%d" % (urllib.parse.quote(asset["id"]), h)
    with acg_open(url, 20) as r:
        data = r.read()
    try:
        os.makedirs(PH_THUMB_DIR, exist_ok=True)
        with open(cache, "wb") as f:
            f.write(data)
    except OSError:
        pass
    return data


def ph_pick_res(levels, wanted):
    """Нужное разрешение, иначе ближайшее меньшее, иначе наименьшее из доступных. levels — ключи вида «4K»."""
    def num(r):
        try:
            return int(str(r).upper().rstrip("K"))
        except ValueError:
            return 0
    have = sorted(levels, key=num)
    if not have:
        return None
    if wanted == "MAX":                                   # наибольшее из доступных у карты
        return have[-1]
    if wanted in have:
        return wanted
    lower = [r for r in have if num(r) < num(wanted)]
    return lower[-1] if lower else have[0]


def ph_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def ph_magic_ok(path, ext):
    with open(path, "rb") as f:
        head = f.read(16)
    if ext == "exr":
        return head[:4] == b"\x76\x2f\x31\x01"
    return head[:2] == b"#?"                              # Radiance: «#?RADIANCE» / «#?RGBE»


class PolyHavenDialog(AmbientCGDialog):
    """Каталог Poly Haven (CC0): тот же интерфейс, что у ambientCG. Скачанные карты попадают в группу «Poly Haven»."""

    SITE = PH_SITE
    TITLE = "Download HDRIs from Poly Haven"
    INFO = "CC0 license: free to use, including in commercial projects. Source: polyhaven.com"
    CATS = PH_CATS
    FOLDER_ATTR, RES_ATTR = "ph_folder", "ph_res"

    def __init__(self, parent, library, on_changed=None):
        super().__init__(parent, library, on_changed)
        self._files_cache = {}
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("File format:"))
        self.cmb_fmt = QtWidgets.QComboBox()
        self.cmb_fmt.addItem("HDR (Radiance): files are 3-4 times smaller", "hdr")
        self.cmb_fmt.addItem("EXR: no precision loss, larger files", "exr")
        self.cmb_fmt.setCurrentIndex(max(0, self.cmb_fmt.findData(self.lib.ph_fmt)))
        self.cmb_fmt.currentIndexChanged.connect(self._on_fmt)
        row.addWidget(self.cmb_fmt)
        row.addStretch(1)
        lay = self.layout()
        lay.insertLayout(lay.count() - 3, row)

    def _on_fmt(self, *_):
        self.lib.ph_fmt = self.cmb_fmt.currentData() or "hdr"

    # --- источник данных ---
    def _fetch(self):
        return ph_from_cache(ph_fetch_catalog())

    def _cached(self):
        items, ts = ph_load_cached_catalog()
        return ph_from_cache(items), ts

    def _save_cat(self, items):
        save_json(PH_CATALOG_FILE, {"version": 1, "fetched": time.time(), "assets": [
            {"id": a["id"], "title": a["title"], "tags": a["tags"], "levels": a["levels"], "date": a["date"],
             "pop": a["pop"], "cat": a["_cat"], "light": sorted(a["_light"])} for a in items]})

    def _prepare(self, a):
        a["_hay"] = (a["title"] + " " + a["id"] + " " + " ".join(a["tags"])).casefold()
        return a

    def _thumb(self, a, big=False):
        return ph_thumb_bytes(a, big)

    @staticmethod
    def _knum(r):
        try:
            return int(str(r).upper().rstrip("K"))
        except ValueError:
            return 0

    def _levels(self, a):
        return list(a["levels"])

    def _avail_res(self, pool):
        """В списке — стандартные 1K..24K и «MAX» (наибольшее у каждой карты: 20K, 29K и т. п.)."""
        have = {r for a in pool for r in a["levels"]}
        out = [("%dK" % k) for k in PH_STD_K if ("%dK" % k) in have]
        if any(self._knum(r) not in PH_STD_K for r in have) or "24K" in have:
            out.append("MAX")
        return out

    def _pick(self, downloads, wanted):
        return ph_pick_res(list(downloads.keys()), wanted)

    def _have_ids(self):
        have = set()
        for p in self.lib.files_of(ALL_GROUP):
            m = re.match(r"^(.+)_\d+k\.(hdr|exr)$", os.path.basename(p), re.I)
            if m:
                have.add(m.group(1).casefold())
        return have

    def _existing(self, a, res, folder):
        fmt = self.cmb_fmt.currentData() or "hdr"
        for ext in (fmt, "exr" if fmt == "hdr" else "hdr"):
            p = os.path.join(folder, "%s_%s.%s" % (a["id"], res.lower(), ext))
            if os.path.isfile(p):
                return p
        return os.path.join(folder, "%s_%s.%s" % (a["id"], res.lower(), fmt))

    def _plan(self):
        """Для выбранных карт спрашивает у API точные ссылки и размеры файлов (запросы идут в потоках)."""
        res = self.cmb_res.currentData()
        sel = self._selected()
        fmt = self.cmb_fmt.currentData() or "hdr"
        need = [a for a in sel if a["id"] not in self._files_cache]
        if need:
            self._info("Getting file links...")
            self.setCursor(QtCore.Qt.WaitCursor)
            QtWidgets.QApplication.processEvents()

            def get(a):
                try:
                    return ph_json("/files/" + urllib.parse.quote(a["id"]), 30).get("hdri") or {}
                except Exception:
                    return None
            try:
                with ThreadPoolExecutor(max_workers=8) as ex:
                    got = list(ex.map(get, need))
            finally:
                self.unsetCursor()
            for a, g in zip(need, got):
                if g:
                    self._files_cache[a["id"]] = g
        plan = []
        for a in sel:
            files = self._files_cache.get(a["id"])
            if not files:
                self._plan_failed.append(a["title"])
                continue
            keys = {k.upper(): k for k in files}
            use = ph_pick_res(list(keys.keys()), res)
            info = files.get(keys.get(use, ""), {}) if use else {}
            ext = fmt if fmt in info else ("exr" if fmt == "hdr" else "hdr")
            f = info.get(ext)
            if not f or not f.get("url"):
                self._plan_failed.append(a["title"])
                continue
            a["_dl"] = {use: (f["url"], int(f.get("size", 0) or 0), str(f.get("md5", "")), ext)}
            plan.append((a, use, f["url"], int(f.get("size", 0) or 0)))
        return plan

    def _get_file(self, a, res, url, size, folder, tmpdir, prog):
        _u, _s, md5, ext = a["_dl"][res]
        dst = os.path.join(folder, "%s_%s.%s" % (a["id"], res.lower(), ext))
        tmp = dst + ".part"
        try:
            if not acg_download(url, tmp, prog):
                return False
            self.progress.setFormat("%s %s: checking file..." % (a["title"], res))
            QtWidgets.QApplication.processEvents()
            if not ph_magic_ok(tmp, ext) or (md5 and ph_md5(tmp) != md5):
                print("[SkyRig Studio] Poly Haven: file %s is damaged (checksum mismatch)" % a["id"])
                return None
            os.replace(tmp, dst)
            return dst
        finally:
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError:
                pass


def _reload_in_place(obj, cls):
    """Перечитывает объект хранилища с диска, сохраняя сам объект (на него ссылаются главное окно и библиотека)."""
    fresh = cls()
    obj.__dict__.clear()
    obj.__dict__.update(fresh.__dict__)


def _fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def _fmt_when(ts):
    try:
        t = time.localtime(float(ts))
    except (TypeError, ValueError):
        return "—"
    now = time.localtime()
    if t.tm_year == now.tm_year and t.tm_yday == now.tm_yday:
        return "today " + time.strftime("%H:%M", t)
    if t.tm_year == now.tm_year and t.tm_yday == now.tm_yday - 1:
        return "yesterday " + time.strftime("%H:%M", t)
    return time.strftime("%b %d, %H:%M", t)


def backup_status_text(lib):
    if not lib.backup_auto:
        return "Auto-backup is off"
    if not lib.backup_last:
        return "No backups yet"
    return "✓ Auto-backup · " + _fmt_when(lib.backup_last)


class BackupDialog(QtWidgets.QDialog):
    """Backup & Restore: настройки автобэкапа, список бэкапов, восстановление."""

    def __init__(self, parent, library, presets=None, sun_cache=None):
        super().__init__(parent)
        self.setWindowTitle("Backup & Restore — HDRI Library")
        self.resize(720, 560)
        self.lib = library
        self.presets = presets
        self.sun_cache = sun_cache
        self.restored_all = False
        self.restored_thumbs = []
        lay = QtWidgets.QVBoxLayout(self)

        box = QtWidgets.QGroupBox("Automatic backup")
        g = QtWidgets.QGridLayout(box)
        self.chk_auto = QtWidgets.QCheckBox("Back up the library automatically")
        self.chk_auto.setChecked(self.lib.backup_auto)
        self.chk_auto.setToolTip("A backup is made after previews are created, after the library changes "
                                 "(at most every 10 minutes) and when the library window is closed.")
        self.chk_auto.toggled.connect(self._on_auto)
        g.addWidget(self.chk_auto, 0, 0, 1, 3)
        g.addWidget(QtWidgets.QLabel("Keep the last"), 1, 0)
        self.sp_keep = QtWidgets.QSpinBox()
        self.sp_keep.setRange(1, 100)
        self.sp_keep.setValue(self.lib.backup_keep)
        self.sp_keep.setSuffix(" backups")
        self.sp_keep.valueChanged.connect(self._on_keep)
        g.addWidget(self.sp_keep, 1, 1)
        g.addWidget(QtWidgets.QLabel("older ones are deleted automatically"), 1, 2)
        lay.addWidget(box)

        box = QtWidgets.QGroupBox("Backup folder")
        v = QtWidgets.QVBoxLayout(box)
        row = QtWidgets.QHBoxLayout()
        self.ed_dir = QtWidgets.QLineEdit(backup_dir(self.lib))
        self.ed_dir.setReadOnly(True)
        b_ch = QtWidgets.QPushButton("Change...")
        b_ch.clicked.connect(self._change_dir)
        b_def = QtWidgets.QPushButton("Default")
        b_def.setToolTip(default_backup_dir())
        b_def.clicked.connect(self._default_dir)
        b_open = QtWidgets.QPushButton("Open")
        b_open.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(backup_dir(self.lib))))
        for w in (self.ed_dir, b_ch, b_def, b_open):
            row.addWidget(w, 1 if w is self.ed_dir else 0)
        v.addLayout(row)
        note = QtWidgets.QLabel("A backup holds the library (groups, tags, brightness, reference), presets, the sun cache "
                                "and the previews. HDRI files are not copied: they stay in their own folders. "
                                "Previews are stored once and shared by all backups, so ten backups take about as much "
                                "space as one.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #a0a0a0;")
        v.addWidget(note)
        lay.addWidget(box)

        box = QtWidgets.QGroupBox("Backups")
        v = QtWidgets.QVBoxLayout(box)
        self.tbl = QtWidgets.QTreeWidget()
        self.tbl.setRootIsDecorated(False)
        self.tbl.setHeaderLabels(["Date", "Type", "Maps", "Previews", "Version"])
        self.tbl.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        v.addWidget(self.tbl, 1)
        row = QtWidgets.QHBoxLayout()
        self.b_missing = QtWidgets.QPushButton("Restore missing previews")
        self.b_missing.setToolTip("Copies back only the previews that are missing now. Your current library is not changed.")
        self.b_missing.clicked.connect(self._restore_missing)
        self.b_all = QtWidgets.QPushButton("Restore everything...")
        self.b_all.setToolTip("Replaces the library, presets and sun cache with the selected backup. "
                              "A backup of the current state is made first, so this can be undone.")
        self.b_all.clicked.connect(self._restore_all)
        self.b_del = QtWidgets.QPushButton("Delete backup")
        self.b_del.clicked.connect(self._delete)
        row.addWidget(self.b_missing)
        row.addWidget(self.b_all)
        row.addStretch(1)
        row.addWidget(self.b_del)
        v.addLayout(row)
        lay.addWidget(box, 1)

        row = QtWidgets.QHBoxLayout()
        self.lbl = QtWidgets.QLabel("")
        self.lbl.setWordWrap(True)
        b_now = QtWidgets.QPushButton("Back up now")
        b_now.clicked.connect(self._backup_now)
        b_close = QtWidgets.QPushButton("Close")
        b_close.clicked.connect(self.accept)
        row.addWidget(self.lbl, 1)
        row.addWidget(b_now)
        row.addWidget(b_close)
        lay.addLayout(row)
        disable_default_buttons(self)
        self._fill()

    def _msg(self, text, error=False):
        self.lbl.setStyleSheet("color: #ff7070;" if error else "")
        self.lbl.setText(text)

    def _fill(self):
        self.tbl.clear()
        items = list_backups(self.lib)
        for path, man in items:
            th = man.get("thumbs") or {}
            it = QtWidgets.QTreeWidgetItem([_fmt_when(man.get("created", 0)), str(man.get("kind", "")),
                                            str(man.get("maps", "")), str(len(th)), str(man.get("app_version", ""))])
            it.setData(0, QtCore.Qt.UserRole, path)
            it.setToolTip(0, path)
            self.tbl.addTopLevelItem(it)
        if items:
            self.tbl.setCurrentItem(self.tbl.topLevelItem(0))
        for i in range(5):
            self.tbl.resizeColumnToContents(i)
        has = bool(items)
        for b in (self.b_missing, self.b_all, self.b_del):
            b.setEnabled(has)
        self._msg(("Backups: %d. " % len(items) if has else "No backups yet. ") + backup_status_text(self.lib))

    def _sel(self):
        it = self.tbl.currentItem()
        return it.data(0, QtCore.Qt.UserRole) if it else None

    def _on_auto(self, on):
        self.lib.backup_auto = bool(on)
        self.lib.save()
        self._fill()

    def _on_keep(self, n):
        self.lib.backup_keep = int(n)
        self.lib.save()

    def _set_dir(self, d):
        self.lib.backup_dir = d
        self.lib.backup_last_hash = ""                 # в новой папке бэкапов ещё нет: следующий сделается сразу
        self.lib.save()
        self.ed_dir.setText(backup_dir(self.lib))
        self._fill()

    def _change_dir(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Backup folder", backup_dir(self.lib))
        if d:
            self._set_dir(os.path.normpath(d))

    def _default_dir(self):
        self._set_dir("")

    def _backup_now(self):
        try:
            path = make_backup(self.lib, "manual")
        except Exception as ex:
            print("[SkyRig Studio] backup error:\n" + traceback.format_exc())
            self._msg("Could not create a backup: %s" % ex, error=True)
            return
        self._fill()
        self._msg("Backup created: %s" % os.path.basename(path))

    def _restore_missing(self):
        try:
            done = restore_missing_thumbs(self.lib)
        except Exception as ex:
            print("[SkyRig Studio] restore error:\n" + traceback.format_exc())
            self._msg("Could not restore previews: %s" % ex, error=True)
            return
        for p in done:
            try:
                make_thumb(p, self.lib.thumb_ev)
            except Exception:
                pass
        self.restored_thumbs.extend(done)
        self._msg("Previews restored: %d." % len(done) if done else "No missing previews were found in the backups.")

    def _restore_all(self):
        path = self._sel()
        if not path:
            return
        r = QtWidgets.QMessageBox.question(
            self, "Restore everything",
            "Replace the current library, presets and sun cache with the backup from %s?\n\n"
            "A backup of the current state is made first, so you can go back." % self.tbl.currentItem().text(0))
        if r != QtWidgets.QMessageBox.Yes:
            return
        try:
            restore_everything(self.lib, path)
            _reload_in_place(self.lib, Library)
            if self.presets is not None:
                _reload_in_place(self.presets, PresetStore)
            if self.sun_cache is not None:
                _reload_in_place(self.sun_cache, SunCache)
            done = restore_missing_thumbs(self.lib)
            for p in done:
                try:
                    make_thumb(p, self.lib.thumb_ev)
                except Exception:
                    pass
        except Exception as ex:
            print("[SkyRig Studio] restore error:\n" + traceback.format_exc())
            self._msg("Could not restore: %s" % ex, error=True)
            return
        self.restored_all = True
        self._fill()
        self._msg("Library restored. Previews restored: %d." % len(done))

    def _delete(self):
        path = self._sel()
        if not path:
            return
        if QtWidgets.QMessageBox.question(self, "Delete backup", "Delete the backup from %s?"
                                          % self.tbl.currentItem().text(0)) != QtWidgets.QMessageBox.Yes:
            return
        try:
            os.remove(path)
        except OSError as ex:
            self._msg("Could not delete: %s" % ex, error=True)
            return
        _prune_backups(self.lib)
        self._fill()


class LibraryDialog(QtWidgets.QDialog):
    """Браузер локальной библиотеки: группы слева, файлы с миниатюрами справа."""

    def __init__(self, parent, library, apply_cb, current_path=""):
        super().__init__(parent)
        self.setObjectName(LIBRARY_OBJ_NAME)
        self.setWindowTitle("HDRI Library")
        self.resize(980, 640)
        self.lib = library
        self.apply_cb = apply_cb
        self.owner = parent
        self.current_path = current_path
        self._cancel = False
        self._busy = False
        self._build()
        disable_default_buttons(self)
        self._fill_groups()

    # ---------- интерфейс ----------
    # Кнопки сгруппированы по задачам (v0.73):
    #   верх — найти и получить карты (Add, Download, поиск, фильтр, сортировка, размер, меню Library);
    #   слева — группы и «★ Favorites»; над сеткой — действия с выделенными картами (видны только при выделении);
    #   справа — инспектор: выбранная карта и солнце, выравнивание яркости, превью; внизу — статус и Apply.
    def _small_btn(self, text, tip, slot, width=26):
        b = QtWidgets.QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setFixedSize(width, 22)
        b.clicked.connect(slot)
        return b

    def _section(self, title):
        box = QtWidgets.QFrame()
        box.setObjectName("insp_sec")
        box.setStyleSheet("QFrame#insp_sec { border: none; border-bottom: 1px solid #333; }")
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(10, 8, 10, 10)
        v.setSpacing(6)
        lbl = QtWidgets.QLabel(title)
        lbl.setStyleSheet("color: %s; font-weight: bold;" % UI_ACCENT)
        v.addWidget(lbl)
        return box, v

    def _build(self):
        self.resize(1240, 760)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # ===== A. верхняя панель: найти и получить карты =====
        top = QtWidgets.QHBoxLayout()
        self.b_add = QtWidgets.QPushButton("+ Add")
        mark_primary(self.b_add)
        m = QtWidgets.QMenu(self.b_add)
        m.addAction("Add files...", self._add_files)
        m.addAction("Add folder...", self._add_folder)
        self.b_add.setMenu(m)
        self.b_dl = QtWidgets.QPushButton("⬇ Download")
        self.b_dl.setToolTip("Free CC0 HDRIs. Downloaded maps go to the “Poly Haven” / “ambientCG” group "
                             "with lighting and resolution tags.")
        m = QtWidgets.QMenu(self.b_dl)
        m.addAction("From Poly Haven...", self._open_ph)
        m.addAction("From ambientCG...", self._open_acg)
        self.b_dl.setMenu(m)
        self.ed_search = QtWidgets.QLineEdit()
        self.ed_search.setPlaceholderText("Search by name or tag...")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.textChanged.connect(self._fill_files)
        self.cmb_tag = QtWidgets.QComboBox()
        self.cmb_tag.setMinimumWidth(140)
        self.cmb_tag.setToolTip("Filter by lighting or resolution tag")
        self.cmb_tag.currentIndexChanged.connect(self._fill_files)
        self.cmb_sort = QtWidgets.QComboBox()
        for label, key in (("Sort: Name", "name"), ("Sort: Name (Z–A)", "name_desc"),
                           ("Sort: Lighting", "light"), ("Sort: Newest file", "newest")):
            self.cmb_sort.addItem(label, key)
        self.cmb_sort.currentIndexChanged.connect(self._fill_files)
        self.sl_size = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sl_size.setRange(50, 125)                          # % от THUMB_W
        self.sl_size.setValue(75)
        self.sl_size.setFixedWidth(90)
        self.sl_size.setToolTip("Thumbnail size")
        self.sl_size.valueChanged.connect(self._on_thumb_size)
        self.b_libmenu = QtWidgets.QPushButton("Library")
        m = QtWidgets.QMenu(self.b_libmenu)
        a_now = m.addAction("Back up now", self._backup_now)
        a_now.setShortcut(QtGui.QKeySequence("Ctrl+B"))
        m.addAction("Restore from backup...", self._open_backup)
        m.addAction("Backup settings...", self._open_backup)
        m.addSeparator()
        m.addAction("Save library to file...", self._export_library)
        m.addAction("Load library from file...", self._import_library)
        m.addSeparator()
        m.addAction("Open backup folder", lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(backup_dir(self.lib))))
        self.b_libmenu.setMenu(m)
        sc = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+B"), self)
        sc.activated.connect(self._backup_now)
        top.addWidget(self.b_add)
        top.addWidget(self.b_dl)
        top.addSpacing(8)
        top.addWidget(self.ed_search, 1)
        top.addWidget(self.cmb_tag)
        top.addWidget(self.cmb_sort)
        top.addWidget(QtWidgets.QLabel("Size"))
        top.addWidget(self.sl_size)
        top.addSpacing(8)
        top.addWidget(self.b_libmenu)
        outer.addLayout(top)
        self._fill_tag_filter()

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.setChildrenCollapsible(False)
        outer.addWidget(split, 1)

        # ===== B. группы =====
        left = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        hdr = QtWidgets.QHBoxLayout()
        lab = QtWidgets.QLabel("GROUPS")
        lab.setStyleSheet("color: #bdbdbd; font-weight: bold;")
        hdr.addWidget(lab)
        hdr.addStretch(1)
        hdr.addWidget(self._small_btn("+", "New group", self._new_group))
        hdr.addWidget(self._small_btn("✎", "Rename group", self._rename_group))
        hdr.addWidget(self._small_btn("✕", "Delete group (files on disk are not deleted)", self._delete_group))
        lv.addLayout(hdr)
        self.lst_groups = QtWidgets.QListWidget()
        self.lst_groups.setMinimumWidth(180)
        self.lst_groups.currentItemChanged.connect(self._on_group_changed)
        self.lst_groups.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.lst_groups.customContextMenuRequested.connect(self._group_menu)
        lv.addWidget(self.lst_groups, 1)
        hint = QtWidgets.QLabel("Right-click a group to rename or delete it.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8f8f8f;")
        lv.addWidget(hint)
        split.addWidget(left)

        # ===== C. сетка + действия с выделенными =====
        center = QtWidgets.QWidget()
        cv = QtWidgets.QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        self.selbar = QtWidgets.QFrame()
        self.selbar.setStyleSheet("QFrame { background: #4a4a4a; border-radius: 3px; }")
        sb = QtWidgets.QHBoxLayout(self.selbar)
        sb.setContentsMargins(8, 4, 8, 4)
        self.lbl_sel = QtWidgets.QLabel("")
        self.lbl_sel.setStyleSheet("font-weight: bold; background: transparent;")
        self.b_togroup = QtWidgets.QPushButton("Add to group...")
        self.b_togroup.clicked.connect(self._copy_to_group)
        self.b_rem = QtWidgets.QPushButton("Remove from group")
        self.b_rem.setToolTip("The file on disk is NOT deleted; it only disappears from this group.")
        self.b_rem.clicked.connect(self._remove_files)
        self.b_fav = QtWidgets.QPushButton("★ Add to Favorites")
        self.b_fav.setStyleSheet("color: #ffc35a;")
        self.b_fav.clicked.connect(self._toggle_favorites)
        b_clr = QtWidgets.QPushButton("Clear selection")
        b_clr.clicked.connect(lambda: self.lst_files.clearSelection())
        for w in (self.lbl_sel, self.b_togroup, self.b_rem, self.b_fav):
            sb.addWidget(w)
        sb.addStretch(1)
        sb.addWidget(b_clr)
        self.selbar.setVisible(False)
        cv.addWidget(self.selbar)

        self.banner = QtWidgets.QFrame()
        self.banner.setStyleSheet("QFrame { background: #4a3d28; border: 1px solid #8a6a32; border-radius: 3px; }"
                                  "QLabel { color: #f0dcb8; border: none; background: transparent; }")
        bl = QtWidgets.QHBoxLayout(self.banner)
        bl.setContentsMargins(10, 6, 10, 6)
        self.lbl_banner = QtWidgets.QLabel("")
        self.lbl_banner.setWordWrap(True)
        b_rb = QtWidgets.QPushButton("Restore from backup")
        mark_primary(b_rb)
        b_rb.clicked.connect(self._restore_from_banner)
        b_rg = QtWidgets.QPushButton("Regenerate")
        b_rg.clicked.connect(lambda: self._make_missing_thumbs())
        bl.addWidget(self.lbl_banner, 1)
        bl.addWidget(b_rb)
        bl.addWidget(b_rg)
        self.banner.setVisible(False)
        cv.addWidget(self.banner)

        self.lst_files = QtWidgets.QListWidget()
        self.lst_files.setViewMode(QtWidgets.QListView.IconMode)
        self.lst_files.setResizeMode(QtWidgets.QListView.Adjust)
        self.lst_files.setMovement(QtWidgets.QListView.Static)
        self.lst_files.setWordWrap(True)
        self.lst_files.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.lst_files.setUniformItemSizes(True)
        self.lst_files.itemDoubleClicked.connect(lambda _i: self._apply())
        self.lst_files.itemSelectionChanged.connect(self._on_selection)
        self.lst_files.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.lst_files.customContextMenuRequested.connect(self._file_menu)
        self._apply_thumb_size()
        cv.addWidget(self.lst_files, 1)
        split.addWidget(center)

        # ===== D. инспектор =====
        insp = QtWidgets.QWidget()
        iv = QtWidgets.QVBoxLayout(insp)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(0)

        sec, v = self._section("Selected map")
        self.lbl_prev = QtWidgets.QLabel()
        self.lbl_prev.setFixedHeight(130)
        self.lbl_prev.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_prev.setStyleSheet("background: #2e2e2e; border-radius: 3px;")
        v.addWidget(self.lbl_prev)
        self.lbl_mapname = QtWidgets.QLabel("—")
        self.lbl_mapname.setWordWrap(True)
        self.lbl_mapname.setStyleSheet("color: #ffffff; font-weight: bold;")
        self.lbl_mapname.setMinimumHeight(18)
        v.addWidget(self.lbl_mapname)
        self.lbl_mapinfo = QtWidgets.QLabel("")
        self.lbl_mapinfo.setStyleSheet("color: #b8b8b8;")
        self.lbl_mapinfo.setWordWrap(True)
        self.lbl_mapinfo.setMinimumHeight(72)
        self.lbl_mapinfo.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        v.addWidget(self.lbl_mapinfo)
        row = QtWidgets.QHBoxLayout()
        self.b_sun = QtWidgets.QPushButton("☀ Detect sun...")
        self.b_sun.setToolTip("Finds the sun position on the maps in advance. When such a map is applied, the sun is "
                              "aligned with it immediately — no need to press “link to HDRI”.")
        self._sun_menu = QtWidgets.QMenu(self.b_sun)
        self._sun_menu.aboutToShow.connect(self._fill_sun_menu)
        self.b_sun.setMenu(self._sun_menu)
        row.addWidget(self.b_sun)
        row.addStretch(1)
        v.addLayout(row)
        self.chk_sun_auto = QtWidgets.QCheckBox("Align the sun with the HDRI when a map is applied")
        self.chk_sun_auto.setToolTip("If the scene has a Corona Sun, it is turned to the sun on the new map right away. "
                                     "Maps without a distinct sun (overcast, night) leave the sun where it is.")
        self.chk_sun_auto.setChecked(getattr(self.lib, "sun_auto_align", True))
        self.chk_sun_auto.toggled.connect(self._on_sun_auto)
        v.addWidget(self.chk_sun_auto)
        iv.addWidget(sec)

        sec, v = self._section("Exposure balance")
        self.lbl_norm = QtWidgets.QLabel("")
        self.lbl_norm.setWordWrap(True)
        self.lbl_norm.setStyleSheet("color: #b0b0b0;")
        v.addWidget(self.lbl_norm)
        row = QtWidgets.QHBoxLayout()
        b_norm = QtWidgets.QPushButton("Balance brightness")
        b_norm.setToolTip("Measures how much light each HDRI in the library gives and calculates an exposure correction "
                          "relative to the reference. After that, Exposure is set automatically when you pick a map.")
        b_norm.clicked.connect(self._equalize)
        b_norm0 = QtWidgets.QPushButton("Reset")
        b_norm0.setToolTip("Turns balancing off: Exposure returns to 0 (for the current map and for maps picked "
                           "later). Measurements are kept.")
        b_norm0.clicked.connect(self._reset_norm)
        row.addWidget(b_norm)
        row.addWidget(b_norm0)
        v.addLayout(row)
        b_ref = QtWidgets.QPushButton("Set selected as reference")
        b_ref.setToolTip("Make the map selected in the list the reference (corrections are recalculated immediately).")
        b_ref.clicked.connect(self._set_reference)
        v.addWidget(b_ref)
        self.chk_norm_sun = QtWidgets.QCheckBox("Include the sun when measuring")
        self.chk_norm_sun.setToolTip("Off: brightness is measured from the sky, without the sun disc (you place the sun "
                                     "separately with Corona Sun). On: everything on the map is counted.")
        self.chk_norm_sun.setChecked(self.lib.norm_sun)
        self.chk_norm_sun.toggled.connect(self._on_norm_sun)
        v.addWidget(self.chk_norm_sun)
        iv.addWidget(sec)

        sec, v = self._section("Previews")
        row = QtWidgets.QHBoxLayout()
        self.b_thumbs = QtWidgets.QPushButton("Create")
        self.b_thumbs.setToolTip("Creates missing previews (once; afterwards they come from the cache). "
                                 "Lighting tags are added at the same time.")
        self.b_thumbs.clicked.connect(lambda: self._make_missing_thumbs())
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(True)
        self.progress.setAlignment(QtCore.Qt.AlignCenter)
        self.progress.setFormat("")
        self.b_cancel = QtWidgets.QPushButton("Cancel")
        self.b_cancel.setEnabled(False)
        self.b_cancel.clicked.connect(self._on_cancel)
        row.addWidget(self.b_thumbs)
        row.addWidget(self.progress, 1)
        row.addWidget(self.b_cancel)
        v.addLayout(row)
        note = QtWidgets.QLabel("Lighting tags (Morning, Noon, Evening, Overcast...) are added automatically "
                                "while previews are created.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #8f8f8f;")
        v.addWidget(note)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Display exposure"))
        self.sl_ev = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        steps = int(THUMB_EV_RANGE * 4)
        self.sl_ev.setRange(-steps, steps)                       # шаг 0.25 EV
        self.sl_ev.setValue(int(round(self.lib.thumb_ev * 4)))
        self.sl_ev.setTracking(False)                            # пересчёт только при отпускании
        self.sl_ev.valueChanged.connect(self._on_ev_changed)
        self.sl_ev.sliderMoved.connect(lambda v_: self.lbl_ev.setText("%+.2f EV" % (v_ / 4.0)))
        self.lbl_ev = QtWidgets.QLabel("%+.2f EV" % self.lib.thumb_ev)
        self.lbl_ev.setMinimumWidth(60)
        b_ev0 = QtWidgets.QPushButton("0")
        b_ev0.setMaximumWidth(28)
        b_ev0.setToolTip("Reset display exposure")
        b_ev0.clicked.connect(lambda: self.sl_ev.setValue(0))
        row.addWidget(self.sl_ev, 1)
        row.addWidget(self.lbl_ev)
        row.addWidget(b_ev0)
        v.addLayout(row)
        b_light = QtWidgets.QPushButton("Re-detect lighting tags...")
        b_light.setToolTip("Tags HDRIs again: morning, noon, afternoon, evening, night, overcast "
                           "(for maps that got previews in older versions).")
        b_light.clicked.connect(self._detect_light)
        v.addWidget(b_light)
        iv.addWidget(sec)
        iv.addStretch(1)
        insp.setMinimumHeight(insp.sizeHint().height() + 40)   # подписи с переносом не сжимаются друг на друга

        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(insp)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(300)
        scroll.setMaximumWidth(380)
        split.addWidget(scroll)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([200, 700, 330])

        # ===== E. низ: статус и выход =====
        row = QtWidgets.QHBoxLayout()
        self.lbl_info = QtWidgets.QLabel("")
        self.lbl_info.setWordWrap(True)
        self.lbl_backup = ClickableLabel("")
        self.lbl_backup.setToolTip("Backup & Restore...")
        self.lbl_backup.clicked.connect(self._open_backup)
        b_close = QtWidgets.QPushButton("Close")
        b_close.setMinimumHeight(30)
        b_close.clicked.connect(self.accept)
        b_apply = QtWidgets.QPushButton("Apply to scene")
        b_apply.setMinimumHeight(30)
        mark_primary(b_apply)
        b_apply.clicked.connect(self._apply)
        row.addWidget(self.lbl_info, 1)
        row.addWidget(self.lbl_backup)
        row.addSpacing(12)
        row.addWidget(b_close)
        row.addWidget(b_apply)
        outer.addLayout(row)
        self._update_norm_label()
        self._update_backup_label()
        self._update_inspector()

    # ---------- новые элементы интерфейса (v0.73) ----------
    def _apply_thumb_size(self):
        k = self.sl_size.value() / 100.0 if hasattr(self, "sl_size") else 0.75
        w, h = int(THUMB_W * k), int(THUMB_H * k)
        self.lst_files.setIconSize(QtCore.QSize(w, h))
        self.lst_files.setGridSize(QtCore.QSize(w + 24, h + 44))

    def _on_thumb_size(self, *_):
        self._apply_thumb_size()

    def _on_selection(self):
        paths = self._selected_paths()
        n = len(paths)
        self.selbar.setVisible(n > 0)
        if n:
            self.lbl_sel.setText("%d selected" % n)
            allfav = all(self.lib.is_favorite(p) for p in paths)
            self.b_fav.setText("☆ Remove from Favorites" if allfav else "★ Add to Favorites")
            g = self._current_group()
            self.b_rem.setText("Remove from Favorites" if g == FAV_GROUP else
                               ("Remove from library" if g == ALL_GROUP else "Remove from group"))
            self.b_rem.setVisible(g != FAV_GROUP)
        self._update_inspector()

    def _toggle_favorites(self):
        paths = self._selected_paths()
        if not paths:
            return
        on = not all(self.lib.is_favorite(p) for p in paths)
        self.lib.set_favorite(paths, on)
        self._save()
        self._fill_groups(select=self._current_group())
        self._info(("Added to Favorites: %d." if on else "Removed from Favorites: %d.") % len(paths))

    def _backup_now(self):
        if self._busy:
            return
        try:
            path = make_backup(self.lib, "manual")
        except Exception as ex:
            print("[SkyRig Studio] backup error:\n" + traceback.format_exc())
            self._info("Could not create a backup: %s" % ex, error=True)
            return
        self._update_backup_label()
        self._info("Backup created: %s" % os.path.basename(path))

    def _group_menu(self, pos):
        it = self.lst_groups.itemAt(pos)
        if it is None:
            return
        self.lst_groups.setCurrentItem(it)
        g = it.data(QtCore.Qt.UserRole)
        m = QtWidgets.QMenu(self)
        a1 = m.addAction("Rename...", self._rename_group)
        a2 = m.addAction("Delete...", self._delete_group)
        for a in (a1, a2):
            a.setEnabled(g not in (ALL_GROUP, FAV_GROUP))
        m.addSeparator()
        m.addAction("New group...", self._new_group)
        m.exec(self.lst_groups.viewport().mapToGlobal(pos))

    def _file_menu(self, pos):
        it = self.lst_files.itemAt(pos)
        if it is not None and not it.isSelected():
            self.lst_files.clearSelection()
            it.setSelected(True)
        paths = self._selected_paths()
        if not paths:
            return
        m = QtWidgets.QMenu(self)
        m.addAction("Apply to scene", self._apply)
        m.addSeparator()
        allfav = all(self.lib.is_favorite(p) for p in paths)
        m.addAction("☆ Remove from Favorites" if allfav else "★ Add to Favorites", self._toggle_favorites)
        m.addAction("Add to group...", self._copy_to_group)
        if self._current_group() != FAV_GROUP:
            m.addAction(self.b_rem.text() + "...", self._remove_files)
        m.addSeparator()
        m.addAction("☀ Detect sun on selected", lambda: self._detect_sun([p for p in paths if os.path.isfile(p)]))
        m.addAction("Open file location", lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(os.path.dirname(paths[0]))))
        m.exec(self.lst_files.viewport().mapToGlobal(pos))

    def _update_inspector(self):
        if not hasattr(self, "lbl_prev"):
            return
        it = self.lst_files.currentItem() if hasattr(self, "lst_files") else None
        sel = self._selected_paths() if hasattr(self, "lst_files") else []
        p = (it.data(QtCore.Qt.UserRole) if it is not None and it.isSelected() else (sel[0] if sel else None))
        if not p:
            self.lbl_prev.setPixmap(QtGui.QPixmap())
            self.lbl_prev.setText("Select a map")
            self.lbl_mapname.setText("—")
            self.lbl_mapinfo.setText("")
            return
        self.lbl_mapname.setText(("★ " if self.lib.is_favorite(p) else "") + os.path.basename(p))
        if not os.path.isfile(p):
            self.lbl_prev.setPixmap(QtGui.QPixmap())
            self.lbl_prev.setText("File not found")
            self.lbl_mapinfo.setText(p)
            return
        png = png_path(p, self.lib.thumb_ev)
        pm = QtGui.QPixmap(png) if os.path.isfile(png) else QtGui.QPixmap()
        if pm.isNull():
            self.lbl_prev.setPixmap(QtGui.QPixmap())
            self.lbl_prev.setText("No preview yet")
        else:
            self.lbl_prev.setText("")
            self.lbl_prev.setPixmap(pm.scaled(self.lbl_prev.width() or 290, 130, QtCore.Qt.KeepAspectRatio,
                                              QtCore.Qt.SmoothTransformation))
        lines = []
        try:
            r = res_label(p)
            if r:
                lines.append("Resolution: %s" % r)
        except Exception:
            pass
        light = self.lib.auto_light.get(path_key(p))
        if light:
            lines.append("Lighting: %s" % light)
        cache = self._sun_cache()
        c = None
        try:
            c = cache.get(p) if cache is not None else None
        except Exception:
            c = None
        if c is None:
            lines.append("Sun: not detected yet")
        elif float(c.get("peak", 0)) >= SUN_MIN_PEAK:
            az, el = hdri_pixel_to_direction(c["u"], c["v"], 0.0)
            lines.append("Sun: found (on the map az %.1f° · el %.1f°)" % (az % 360.0, el))
        else:
            lines.append("Sun: no distinct sun on this map")
        off = self.lib.norm_offset(p, ignore_enabled=True)
        if off is not None:
            lines.append("Brightness correction: %+.2f EV" % off)
        self.lbl_mapinfo.setText("\n".join(lines))

    def _info(self, text, error=False):
        self.lbl_info.setStyleSheet("color: #ff7070;" if error else "")
        self.lbl_info.setText(text)

    def _save(self, force_backup=False):
        try:
            self.lib.save()
        except Exception:
            print("[SkyRig Studio] library save error:\n" + traceback.format_exc())
            self._info("Could not save the library (see Listener).", error=True)
            return
        save_thumb_index()
        if maybe_auto_backup(self.lib, force=force_backup):
            self._update_backup_label()

    # ---------- солнце ----------
    @staticmethod
    def _fav_badge(pm):
        """Звёздочка «в избранном» в левом нижнем углу превью."""
        pm = pm.copy()
        p = QtGui.QPainter(pm)
        try:
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            r = max(8, pm.height() // 7)
            cx, cy = r + 6, pm.height() - r - 5
            pts = []
            for i in range(10):
                ang = math.radians(-90 + i * 36)
                rr = r if i % 2 == 0 else r * 0.45
                pts.append(QtCore.QPointF(cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
            p.setPen(QtGui.QPen(QtGui.QColor(20, 20, 20, 220), 1.5))
            p.setBrush(QtGui.QColor("#ffc35a"))
            p.drawPolygon(QtGui.QPolygonF(pts))
        finally:
            p.end()
        return pm

    @staticmethod
    def _sun_badge(pm):
        """Значок «солнце найдено» в правом верхнем углу превью."""
        pm = pm.copy()
        p = QtGui.QPainter(pm)
        try:
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            r = max(7, pm.height() // 9)
            cx, cy = pm.width() - r - 6, r + 6
            p.setPen(QtGui.QPen(QtGui.QColor(20, 20, 20, 200), 2))
            p.setBrush(QtGui.QColor(UI_ACCENT))
            p.drawEllipse(QtCore.QPointF(cx, cy), r, r)
        finally:
            p.end()
        return pm

    def _sun_cache(self):
        return getattr(self.owner, "sun_cache", None)

    def _on_sun_auto(self, on):
        self.lib.sun_auto_align = bool(on)
        self._save()

    def _fill_sun_menu(self):
        m = self._sun_menu
        m.clear()
        sel = [p for p in self._selected_paths() if os.path.isfile(p)]
        allf = [p for p in self.lib.files_of(ALL_GROUP) if os.path.isfile(p)]
        a = m.addAction("On selected maps (%d)" % len(sel))
        a.setEnabled(bool(sel))
        a.triggered.connect(lambda *_: self._detect_sun(sel))
        b = m.addAction("On all maps (%d)" % len(allf))
        b.setEnabled(bool(allf))
        b.triggered.connect(lambda *_: self._detect_sun(allf))

    def _detect_sun(self, files):
        cache = self._sun_cache()
        if self._busy or cache is None:
            return
        todo = [p for p in files if cache.get(p) is None]
        if not todo:
            self._info("The sun position is already known for all %d map(s)." % len(files))
            return
        self._busy, self._cancel = True, False
        self.b_thumbs.setEnabled(False)
        self.b_sun.setEnabled(False)
        self.b_cancel.setEnabled(True)
        found = no_sun = failed = 0
        try:
            for i, p in enumerate(todo, 1):
                if self._cancel:
                    break
                base_pc = (i - 1) * 100.0 / len(todo)

                def prog(pc, _text, base_pc=base_pc, i=i):
                    self.progress.setValue(int(base_pc + pc / len(todo)))
                    self.progress.setFormat("Sun: %d of %d" % (i, len(todo)))
                    QtWidgets.QApplication.processEvents()
                    return not self._cancel

                try:
                    res = find_brightest(p, prog)
                except Exception:
                    print("[SkyRig Studio] find brightest error for %s:\n%s" % (p, traceback.format_exc()))
                    res = None
                if res is None:
                    if not self._cancel:
                        failed += 1
                    continue
                cache.put(p, *res)
                if res[2] >= SUN_MIN_PEAK:
                    found += 1
                else:
                    no_sun += 1
            self.progress.setValue(100 if not self._cancel else self.progress.value())
            self.progress.setFormat(("Cancelled" if self._cancel else "Done") + ": sun found on %d of %d" % (found, len(todo)))
        finally:
            self._busy = False
            self.b_thumbs.setEnabled(True)
            self.b_sun.setEnabled(True)
            self.b_cancel.setEnabled(False)
        save_thumb_index()
        self._fill_files()
        msg = "Sun found: %d." % found
        if no_sun:
            msg += " No distinct sun (overcast, night...): %d." % no_sun
        if failed:
            msg += " Could not read: %d." % failed
        self._info(msg)

    # ---------- бэкап ----------
    def _update_backup_label(self):
        if hasattr(self, "lbl_backup"):
            t = backup_status_text(self.lib)
            self.lbl_backup.setText(t)
            self.lbl_backup.setStyleSheet("color: #9fc79f;" if t.startswith("✓") else "color: #b0b0b0;")

    def _open_backup(self):
        if self._busy:
            return
        save_thumb_index()
        owner = self.owner
        dlg = BackupDialog(self, self.lib, getattr(owner, "presets", None), getattr(owner, "sun_cache", None))
        dlg.exec()
        if dlg.restored_all:
            self._fill_groups()
            self._fill_tag_filter()
            self._update_norm_label()
            self.chk_norm_sun.setChecked(self.lib.norm_sun)
            for fn in ("_refresh_presets", "_fill_presets"):
                if hasattr(owner, fn):
                    try:
                        getattr(owner, fn)()
                    except Exception:
                        pass
                    break
        elif dlg.restored_thumbs:
            self._fill_files()
        self._update_backup_label()

    def _restore_from_banner(self):
        if self._busy:
            return
        try:
            done = restore_missing_thumbs(self.lib, self.lib.files_of(self._current_group()))
        except Exception as ex:
            print("[SkyRig Studio] restore error:\n" + traceback.format_exc())
            self._info("Could not restore previews: %s" % ex, error=True)
            return
        for p in done:
            try:
                make_thumb(p, self.lib.thumb_ev)
            except Exception:
                pass
        self._fill_files()
        self._info("Previews restored from backup: %d." % len(done))

    def done(self, r):
        try:
            self._save(force_backup=True)              # при закрытии — бэкап, если что-то изменилось
        except Exception:
            pass
        super().done(r)

    # ---------- группы ----------
    def _current_group(self):
        it = self.lst_groups.currentItem()
        return it.data(QtCore.Qt.UserRole) if it else ALL_GROUP

    def _fill_groups(self, select=None):
        self.lst_groups.blockSignals(True)
        self.lst_groups.clear()
        entries = [FAV_GROUP, ALL_GROUP] + self.lib.names()
        for n in entries:
            cnt = len(self.lib.files_of(n))
            it = QtWidgets.QListWidgetItem("%s  (%d)" % (n, cnt))
            it.setData(QtCore.Qt.UserRole, n)
            if n == FAV_GROUP:
                it.setForeground(QtGui.QBrush(QtGui.QColor("#ffc35a")))
                it.setToolTip("Maps you starred. Select maps and press “★ Add to Favorites” (or right-click).")
            self.lst_groups.addItem(it)
        want = select or self.lib.last_group or ALL_GROUP
        row = entries.index(want) if want in entries else 0
        self.lst_groups.setCurrentRow(row)
        self.lst_groups.blockSignals(False)
        self._fill_files()

    def _on_group_changed(self, *_):
        self.lib.last_group = self._current_group()
        self._fill_files()

    def _new_group(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "New group", "Group name:")
        if ok and name.strip():
            final = self.lib.add_group(name)
            self._save()
            self._fill_groups(select=final)

    def _rename_group(self):
        g = self._current_group()
        if g in (ALL_GROUP, FAV_GROUP):
            self._info("Group “%s” cannot be renamed." % g)
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Rename group", "New name:", text=g)
        if ok and name.strip():
            if self.lib.rename_group(g, name.strip()):
                self._save()
                self._fill_groups(select=name.strip())
            else:
                self._info("This name is invalid or already taken.", error=True)

    def _delete_group(self):
        g = self._current_group()
        if g in (ALL_GROUP, FAV_GROUP):
            self._info("Group “%s” cannot be deleted." % g)
            return
        r = QtWidgets.QMessageBox.question(
            self, "Delete group",
            "Delete group “%s”?\nHDRI files on disk are not deleted." % g)
        if r == QtWidgets.QMessageBox.Yes:
            self.lib.delete_group(g)
            self.lib.last_group = ""
            self._save()
            self._fill_groups()

    def _ask_group(self, title, allow_new=True):
        names = self.lib.names()
        NEW = "+ New group..."
        items = names + ([NEW] if allow_new else [])
        if not items:
            return None
        choice, ok = QtWidgets.QInputDialog.getItem(self, title, "Group:", items, 0, False)
        if not ok:
            return None
        if choice == NEW:
            name, ok2 = QtWidgets.QInputDialog.getText(self, "New group", "Group name:")
            if not ok2 or not name.strip():
                return None
            choice = self.lib.add_group(name)
        return choice

    # ---------- файлы ----------
    def _fill_tag_filter(self):
        cur = self.cmb_tag.currentData() if self.cmb_tag.count() else ""
        self.cmb_tag.blockSignals(True)
        self.cmb_tag.clear()
        self.cmb_tag.addItem("All tags", "")
        for t in self.lib.all_tags():
            self.cmb_tag.addItem(t, t)
        i = self.cmb_tag.findData(cur) if cur else 0
        self.cmb_tag.setCurrentIndex(max(0, i))
        self.cmb_tag.blockSignals(False)

    def _edit_tags(self):
        paths = self._selected_paths()
        if not paths:
            self._info("Select files to tag.")
            return
        cur = []
        for p in paths:
            for t in self.lib.tags_of(p):
                if t.casefold() not in {c.casefold() for c in cur}:
                    cur.append(t)
        dlg = TagsDialog(self, len(paths), cur, self.lib.all_tags())
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        tags, mode = dlg.result_tags()
        if not tags and mode != "replace":
            return
        self.lib.edit_tags(paths, tags, mode)
        self._save()
        self._fill_tag_filter()
        self._fill_files()
        self._info("Tags updated on files: %d." % len(paths))

    def _fill_files(self, *_):
        q = self.ed_search.text().strip().lower()
        tag_f = (self.cmb_tag.currentData() or "").casefold() if hasattr(self, "cmb_tag") else ""
        group = self._current_group()
        files = self.lib.files_of(group)
        key = self.cmb_sort.currentData() if hasattr(self, "cmb_sort") else "name"
        if key in ("name", "name_desc"):
            files = sorted(files, key=lambda f: os.path.basename(f).casefold(), reverse=(key == "name_desc"))
        elif key == "light":
            order = {c: i for i, c in enumerate(LIGHT_CLASSES)}
            files = sorted(files, key=lambda f: (order.get(self.lib.auto_light.get(path_key(f)), 99),
                                                 os.path.basename(f).casefold()))
        elif key == "newest":
            def _mt(f):
                try:
                    return os.path.getmtime(f)
                except OSError:
                    return 0.0
            files = sorted(files, key=_mt, reverse=True)
        sel_keys = {path_key(x) for x in self._selected_paths()} if hasattr(self, "lst_files") else set()
        self.lst_files.setUpdatesEnabled(False)
        self.lst_files.clear()
        sun_cache = self._sun_cache() if hasattr(self, "owner") else None
        missing = 0
        no_thumb = 0
        for p in files:
            base = os.path.basename(p)
            ptags = self.lib.tags_of(p)
            if tag_f and tag_f not in {t.casefold() for t in ptags}:
                continue
            if q and q not in base.lower() and not any(q in t.lower() for t in ptags):
                continue
            exists = os.path.isfile(p)
            sun = None
            if exists and sun_cache is not None:
                try:
                    sun = sun_cache.get(p)
                except Exception:
                    sun = None
            has_sun = bool(sun) and float(sun.get("peak", 0)) >= SUN_MIN_PEAK
            it = QtWidgets.QListWidgetItem(base)
            it.setData(QtCore.Qt.UserRole, p)
            png = png_path(p, self.lib.thumb_ev)
            if exists and os.path.isfile(png):
                pm = QtGui.QPixmap(png)
                if not pm.isNull():
                    if has_sun:
                        pm = self._sun_badge(pm)
                    if self.lib.is_favorite(p):
                        pm = self._fav_badge(pm)
                    it.setIcon(QtGui.QIcon(pm))
            elif exists:
                no_thumb += 1
            if not exists:
                missing += 1
                it.setForeground(QtGui.QBrush(QtGui.QColor("#ff7070")))
                it.setToolTip("File not found:\n" + p)
            else:
                tip = p + ("\nTags: " + ", ".join(ptags) if ptags else "")
                if sun:
                    tip += "\nSun: found" if has_sun else "\nSun: no distinct sun on this map"
                it.setToolTip(tip)
            if path_key(p) == path_key(self.current_path or ""):
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            self.lst_files.addItem(it)
            if path_key(p) in sel_keys:
                it.setSelected(True)
        self.lst_files.setUpdatesEnabled(True)
        if hasattr(self, "selbar"):
            self._on_selection()
        n_rest = 0
        if no_thumb and hasattr(self, "banner"):
            try:
                n_rest = len(missing_thumbs_in_backup(self.lib, files))
            except Exception:
                n_rest = 0
        if n_rest:
            self.lbl_banner.setText("⚠  <b>%d preview%s missing.</b> The backup from %s has %s." % (
                n_rest, " is" if n_rest == 1 else "s are", _fmt_when(self.lib.backup_last),
                "it" if n_rest == 1 else "them"))
        if hasattr(self, "banner"):
            self.banner.setVisible(bool(n_rest))
        msg = "Files: %d" % len(files)
        if no_thumb:
            msg += ", without preview: %d" % no_thumb
        if missing:
            msg += ", not found on disk: %d" % missing
        self._info(msg)

    def _selected_paths(self):
        return [it.data(QtCore.Qt.UserRole) for it in self.lst_files.selectedItems()]

    def _target_group_for_add(self):
        g = self._current_group()
        if g not in (ALL_GROUP, FAV_GROUP):
            return g
        return self._ask_group("Which group should they be added to?")

    def _add_files(self):
        g = self._target_group_for_add()
        if not g:
            return
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Add HDRI / EXR", "", "HDRI (*.hdr *.exr);;All files (*.*)")
        if files:
            n = self.lib.add_files(g, files)
            self._save()
            self._fill_groups(select=g)
            self._info("Added: %d. Creating previews..." % n)
            self._make_missing_thumbs(only=files)

    def _export_library(self):
        if not self.lib.groups:
            self._info("The library is empty: nothing to save.", error=True)
            return
        default = os.path.join(os.path.expanduser("~"), "SkyRigStudio_library.json")
        path, _f = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save HDRI library", default, "SkyRig Studio library (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            self.lib.export_to(path)
        except Exception:
            print("[SkyRig Studio] library export error:\n" + traceback.format_exc())
            self._info("Could not save the file (see Listener).", error=True)
            return
        total = len(self.lib.files_of(ALL_GROUP))
        self._info("Library saved: groups %d, files %d.\n%s" % (len(self.lib.groups), total, path))

    def _import_library(self):
        path, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load HDRI library", os.path.expanduser("~"), "SkyRig Studio library (*.json)")
        if not path:
            return
        try:
            groups, tags = Library.read_backup(path)
        except ValueError as ex:
            self._info("Not loaded: %s." % ex, error=True)
            return
        if not groups:
            self._info("The file contains no groups.", error=True)
            return
        n_files = len({path_key(f) for g in groups for f in g["files"]})
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Load library")
        box.setText("In the file: groups %d, files %d.\n\nMerge with the current library or replace it?"
                    % (len(groups), n_files))
        b_merge = box.addButton("Merge", QtWidgets.QMessageBox.AcceptRole)
        b_repl = box.addButton("Replace", QtWidgets.QMessageBox.DestructiveRole)
        box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (b_merge, b_repl):
            return
        ng, nf = self.lib.import_groups(groups, replace=(clicked is b_repl), tags=tags)
        self._save()
        self._fill_tag_filter()
        self._fill_groups()
        self._info(("Library replaced" if clicked is b_repl else "Library merged")
                   + ": groups %d, files %d. Previews are created with the “Create previews” button." % (ng, nf))

    def _add_folder(self):
        g = self._target_group_for_add()
        if not g:
            return
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Folder with HDRIs")
        if not d:
            return
        rec = QtWidgets.QMessageBox.question(
            self, "Add folder", "Include subfolders?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) == QtWidgets.QMessageBox.Yes
        found = []
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            if rec:
                for root_dir, _dirs, fs in os.walk(d):
                    for f in fs:
                        if f.lower().endswith(HDRI_EXTS):
                            found.append(os.path.join(root_dir, f))
                    if len(found) > 20000:
                        break
            else:
                for f in os.listdir(d):
                    fp = os.path.join(d, f)
                    if os.path.isfile(fp) and f.lower().endswith(HDRI_EXTS):
                        found.append(fp)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        if not found:
            self._info("No .hdr / .exr files in the folder.")
            return
        if len(found) > 300:
            r = QtWidgets.QMessageBox.question(
                self, "Add folder", "Files found: %d. Add all?" % len(found))
            if r != QtWidgets.QMessageBox.Yes:
                return
        found.sort(key=lambda p: p.lower())
        n = self.lib.add_files(g, found)
        self._save()
        self._fill_groups(select=g)
        self._info("New files added: %d. Previews are created with the “Create previews” button." % n)

    def _remove_files(self):
        paths = self._selected_paths()
        g = self._current_group()
        if not paths:
            self._info("Select files in the list.")
            return
        if g == FAV_GROUP:
            self.lib.set_favorite(paths, False)
            self._save()
            self._fill_groups(select=g)
            return
        extra = "\nFiles will be removed from ALL groups." if g == ALL_GROUP else ""
        r = QtWidgets.QMessageBox.question(
            self, "Remove from group",
            "Remove the selected files (%d) from “%s”?\nFiles on disk are NOT deleted.%s" % (len(paths), g, extra))
        if r == QtWidgets.QMessageBox.Yes:
            self.lib.remove_files(g, paths)
            self._save()
            self._fill_groups(select=g)

    def _copy_to_group(self):
        paths = self._selected_paths()
        if not paths:
            self._info("Select files in the list.")
            return
        target = self._ask_group("Add selected to group")
        if not target:
            return
        n = self.lib.add_files(target, paths)
        self._save()
        self._fill_groups(select=self._current_group())
        self._info("Added to “%s”: %d." % (target, n))

    # ---------- превью ----------
    def _on_cancel(self):
        self._cancel = True
        self.b_cancel.setEnabled(False)

    def _make_missing_thumbs(self, only=None, *_):
        if self._busy:
            return
        if only is not None and not isinstance(only, (list, tuple)):
            only = None
        files = only if only else self.lib.files_of(self._current_group())
        todo = [p for p in files if os.path.isfile(p)
                and not os.path.isfile(png_path(p, self.lib.thumb_ev))]
        if not todo:
            self._info("All previews already exist.")
            return
        self._busy, self._cancel = True, False
        self.b_thumbs.setEnabled(False)
        self.b_cancel.setEnabled(True)
        done = 0
        try:
            for i, p in enumerate(todo, 1):
                if self._cancel:
                    break
                self.progress.setValue(int((i - 1) * 100 / len(todo)))
                self.progress.setFormat("Previews: %d of %d" % (i - 1, len(todo)))
                QtWidgets.QApplication.processEvents()
                try:
                    if make_thumb(p, self.lib.thumb_ev):
                        done += 1
                        if path_key(p) not in self.lib.auto_light:      # теги освещения — сразу при создании превью
                            try:
                                cls, _how = detect_light(p, True)
                                if cls:
                                    self.lib.set_auto_light(p, cls, False)
                            except Exception:
                                print("[SkyRig Studio] light detect error for %s:\n%s" % (p, traceback.format_exc()))
                except Exception:
                    print("[SkyRig Studio] thumb error for %s:\n%s" % (p, traceback.format_exc()))
            self.progress.setValue(100 if not self._cancel else self.progress.value())
            self.progress.setFormat("Done: %d of %d" % (done, len(todo)) if not self._cancel
                                    else "Cancelled: %d of %d" % (done, len(todo)))
        finally:
            self._busy = False
            self.b_thumbs.setEnabled(True)
            self.b_cancel.setEnabled(False)
        if done:
            self._save(force_backup=True)              # новые превью и теги освещения -> сразу в бэкап
            self._fill_tag_filter()
        self._fill_files()

    # ---------- ambientCG ----------
    def _open_acg(self):
        if self._busy:
            return
        dlg = AmbientCGDialog(self, self.lib, on_changed=self._acg_changed)
        dlg.exec()
        self._acg_changed()

    def _open_ph(self):
        if self._busy:
            return
        dlg = PolyHavenDialog(self, self.lib, on_changed=self._acg_changed)
        dlg.exec()
        self._acg_changed()

    def _acg_changed(self):
        self._save()
        self._fill_groups(self._current_group())
        self._fill_files()
        self._fill_tag_filter()
        self._update_norm_label()

    # ---------- выравнивание яркости ----------
    def _update_norm_label(self):
        ref = os.path.basename(self.lib.norm_ref) if self.lib.norm_ref else "none selected"
        n = len(self.lib.brightness)
        self.lbl_norm.setText("Reference: %s. Balancing is %s. Maps measured: %d." % (
            ref, "enabled" if self.lib.norm_enabled else "disabled", n))

    def _find_default_ref(self):
        for p in self.lib.files_of(ALL_GROUP):
            if os.path.basename(p).lower().startswith(DEFAULT_NORM_REF_NAME.lower()):
                return p
        return ""

    def _measure(self, p):
        """Измерение яркости карты (создаёт превью, если его нет). True — измерено."""
        if not os.path.isfile(lin_path(p)):
            if not make_thumb(p, self.lib.thumb_ev):
                return False
        m = brightness_metrics(p)
        if m is None:
            return False
        self.lib.brightness[path_key(p)] = m
        return True

    def _equalize(self):
        if self._busy:
            return
        if not self.lib.norm_ref or not os.path.isfile(self.lib.norm_ref):
            ref = self._find_default_ref()
            if not ref:
                self._info("Reference not found. Select a map in the list and press “Reference = selected”.", error=True)
                return
            self.lib.norm_ref = ref
        files = [p for p in self.lib.files_of(ALL_GROUP) if os.path.isfile(p)]
        if self.lib.norm_ref not in files:
            files.append(self.lib.norm_ref)
        self._busy, self._cancel = True, False
        self.b_thumbs.setEnabled(False)
        self.b_cancel.setEnabled(True)
        ok, failed = 0, 0
        try:
            for i, p in enumerate(files, 1):
                if self._cancel:
                    break
                self.progress.setValue(int((i - 1) * 100 / len(files)))
                self.progress.setFormat("Brightness: %d of %d" % (i - 1, len(files)))
                QtWidgets.QApplication.processEvents()
                try:
                    if self._measure(p):
                        ok += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1
                    print("[SkyRig Studio] brightness error for %s:\n%s" % (p, traceback.format_exc()))
            cancelled = self._cancel
            self.progress.setValue(100 if not cancelled else self.progress.value())
            self.progress.setFormat("Done: %d of %d" % (ok, len(files)) if not cancelled
                                    else "Cancelled: %d of %d" % (ok, len(files)))
        finally:
            self._busy = False
            self.b_thumbs.setEnabled(True)
            self.b_cancel.setEnabled(False)
        if self._cancel:
            self._save()
            return
        if not self.lib.brightness.get(path_key(self.lib.norm_ref)):
            self._info("Could not measure the reference, so balancing is not turned on.", error=True)
            self._save()
            return
        self.lib.norm_enabled = True
        self._save()
        offs = [self.lib.norm_offset(p) for p in files]
        offs = [o for o in offs if o is not None]
        msg = "Maps balanced: %d against reference “%s”" % (len(offs), os.path.basename(self.lib.norm_ref))
        if offs:
            msg += " (corrections from %+.1f to %+.1f EV)" % (min(offs), max(offs))
        if failed:
            msg += ". Could not measure: %d" % failed
        self._info(msg + ".")
        self._update_norm_label()
        try:
            self.owner.apply_norm_to_current()
        except Exception:
            print("[SkyRig Studio] apply norm error:\n" + traceback.format_exc())

    def _reset_norm(self):
        if self._busy:
            return
        self.lib.norm_enabled = False
        self._save()
        self._update_norm_label()
        try:
            self.owner.reset_norm_current()
        except Exception:
            print("[SkyRig Studio] reset norm error:\n" + traceback.format_exc())
        self._info("Balancing turned off, Exposure returned to 0.")

    def _set_reference(self):
        sel = self._selected_paths()
        if len(sel) != 1:
            self._info("Select one map in the list; it will become the reference.", error=True)
            return
        self.lib.norm_ref = sel[0]
        try:
            if not self._measure(sel[0]):
                self._info("Could not measure the reference.", error=True)
                return
        except Exception:
            self._info("Could not measure the reference (see Listener).", error=True)
            return
        self._save()
        self._update_norm_label()
        self._info("Reference: “%s”. Press “Balance brightness” to recalculate the other maps." %
                   os.path.basename(sel[0]))
        if self.lib.norm_enabled:
            try:
                self.owner.refresh_norm_current()
            except Exception:
                pass

    def _on_norm_sun(self, on):
        self.lib.norm_sun = bool(on)
        self._save()
        self._update_norm_label()
        if self.lib.norm_enabled:
            try:
                self.owner.refresh_norm_current()
            except Exception:
                pass

    def _detect_light(self):
        if self._busy:
            return
        sel = self._selected_paths()
        grp = self._current_group()
        dlg = LightDialog(self, len(sel), len(self.lib.files_of(grp)), grp, len(self.lib.files_of(ALL_GROUP)))
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        scope = dlg.scope()
        files = sel if scope == "sel" else self.lib.files_of(grp if scope == "grp" else ALL_GROUP)
        files = [p for p in files if os.path.isfile(p)]
        if not files:
            self._info("No files available for analysis.", error=True)
            return
        use_name, to_groups = dlg.chk_name.isChecked(), dlg.chk_groups.isChecked()
        self._busy, self._cancel = True, False
        self.b_thumbs.setEnabled(False)
        self.b_cancel.setEnabled(True)
        counts, failed, done = {}, 0, 0
        try:
            for i, p in enumerate(files, 1):
                if self._cancel:
                    break
                self.progress.setValue(int((i - 1) * 100 / len(files)))
                self.progress.setFormat("Lighting: %d of %d" % (i - 1, len(files)))
                QtWidgets.QApplication.processEvents()
                try:
                    cls, _how = detect_light(p, use_name)
                except Exception:
                    print("[SkyRig Studio] light detect error for %s:\n%s" % (p, traceback.format_exc()))
                    cls = None
                if cls is None:
                    failed += 1
                    continue
                self.lib.set_auto_light(p, cls, to_groups)
                counts[cls] = counts.get(cls, 0) + 1
                done += 1
                if done % 20 == 0:
                    self._save()
            self.progress.setValue(100 if not self._cancel else self.progress.value())
            self.progress.setFormat("Done: %d of %d" % (done, len(files)) if not self._cancel
                                    else "Cancelled: %d of %d" % (done, len(files)))
        finally:
            self._busy = False
            self.b_thumbs.setEnabled(True)
            self.b_cancel.setEnabled(False)
        self._save()
        self._fill_tag_filter()
        self._fill_groups()
        msg = "Tagged: %d" % done
        if counts:
            msg += " (" + ", ".join("%s — %d" % (c, counts[c]) for c in LIGHT_CLASSES if c in counts) + ")"
        if failed:
            msg += ". Failed: %d" % failed
        self._info(msg)

    def _on_ev_changed(self, v):
        """Новая экспозиция превью: пересчёт из кэша .lin (Max не нужен, быстро)."""
        ev = v / 4.0
        self.lbl_ev.setText("%+.2f EV" % ev)
        if self._busy:
            return
        self.lib.thumb_ev = ev
        self._save()
        files = [p for p in self.lib.files_of(self._current_group()) if os.path.isfile(lin_path(p))]
        self._busy, self._cancel = True, False
        self.b_thumbs.setEnabled(False)
        self.b_cancel.setEnabled(True)
        try:
            for i, p in enumerate(files, 1):
                if self._cancel:
                    break
                self.progress.setValue(int((i - 1) * 100 / max(1, len(files))))
                self.progress.setFormat("Exposure: %d of %d" % (i - 1, len(files)))
                QtWidgets.QApplication.processEvents()
                try:
                    render_thumb(p, ev)
                except Exception:
                    print("[SkyRig Studio] re-render error for %s:\n%s" % (p, traceback.format_exc()))
            self.progress.setValue(100)
            self.progress.setFormat("Done: %d" % len(files))
        finally:
            self._busy = False
            self.b_thumbs.setEnabled(True)
            self.b_cancel.setEnabled(False)
        self._fill_files()

    # ---------- применение ----------
    def _apply(self):
        paths = self._selected_paths()
        if not paths:
            self._info("Select a file in the list.")
            return
        p = paths[0]
        if not os.path.isfile(p):
            self._info("File not found: %s" % p, error=True)
            return
        if self.apply_cb(p):
            self.current_path = p
            self._fill_files()
            self._info("Applied: %s" % os.path.basename(p))


# ==========================================================================
# 7. Главное окно
# ==========================================================================
HIST_LIMIT = 100          # сколько шагов хранит собственная история скрипта
HIST_MERGE_SEC = 1.5      # однотипные правки (например, протяжка спиннера) в этом окне — один шаг


def undoable(label):
    """Метод меняет состояние, которым управляет скрипт: в конце — один шаг в собственной истории
    (вложенные вызовы объединяются в один шаг)."""
    def deco(fn):
        def wrapper(self, *a, **k):
            if getattr(self, "_loading", False):             # идёт чтение из сцены: это не действие пользователя
                return fn(self, *a, **k)
            alog("ACTION %s" % label)
            self._hist_begin()
            try:
                return fn(self, *a, **k)
            finally:
                self._hist_end(label)
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return deco


def _state_equal(a, b, tol=1e-4):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_state_equal(a[k], b[k], tol) for k in a)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) <= tol
    return a == b


def delete_sun_objects():
    """Удаляет солнце, его цель и значок."""
    sun = find_sun()
    ic = find_icon()
    if ic is not None:
        rt.delete(ic)
    if sun is not None:
        tgt = sun.target if bool(sun.targeted) else None
        rt.delete(sun)
        if tgt is not None:
            try:
                rt.delete(tgt)
            except Exception:
                pass


class SunCompass(QtWidgets.QWidget):
    """Диорама-сфера: небесный купол с HDRI (вид изнутри, через прозрачную оболочку), земля, стороны
    света, солнце и направление активной камеры. Мышью вращается вид, двойной клик — вернуть вид.
    Клик — задать азимут солнца (сигнал clicked_az); сам виджет сцену не меняет. Таймеров нет."""

    clicked_az = QtCore.Signal(float)

    SIZE = 236
    HI, LO = 180, 96                      # размер растра: покой / во время вращения

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.setToolTip("Sun on the sky sphere.\n"
                        "Click: place the sun here by azimuth (elevation does not change); the HDRI rotates "
                        "with it (the bright point of the map stays under the sun).\n"
                        "Drag with the mouse: rotate the view; right button: reset the view.\n"
                        "N/E/S/W: compass directions (N = scene +Y). Yellow dot: the sun; light blue sector: "
                        "direction of the active camera.")
        self.show_tex = True
        self.yaw, self.pitch = 200.0, 28.0          # откуда смотрим: азимут и высота
        self.sun = None                              # (азимут, высота)
        self.cam = None                              # (азимут, fov)
        self.rot, self.horizon, self.hor_sign = 0.0, 0.0, 1.0
        self.tex, self.tw, self.th, self.tex_key = None, 0, 0, None
        self._img, self._img_key = None, None
        self._drag = None
        self._low = False

    def set_show_tex(self, on):
        on = bool(on)
        if on != self.show_tex:
            self.show_tex = on
            self._img_key = None
            self.update()

    # ---------- данные ----------
    def set_texture(self, png):
        key = None
        try:
            key = (png, os.path.getmtime(png)) if png and os.path.isfile(png) else None
        except OSError:
            key = None
        if key == self.tex_key:
            return
        self.tex_key = key
        self.tex = None
        if key:
            img = QtGui.QImage(png)
            if not img.isNull():
                img = img.convertToFormat(QtGui.QImage.Format_RGB888)
                self.tw, self.th = img.width(), img.height()
                self.tex = bytes(img.constBits())[: self.tw * self.th * 3]
                if len(self.tex) != self.tw * self.th * 3:
                    self.tex = None
        self._img_key = None
        self.update()

    def set_state(self, sun, rot, horizon, hor_sign, cam):
        st = (None if sun is None else (round(sun[0], 2), round(sun[1], 2)), round(rot, 2),
              round(horizon, 2), None if cam is None else (round(cam[0], 1), round(cam[1], 1)))
        if st == getattr(self, "_st", None):
            return
        self._st = st
        self.sun, self.rot, self.horizon, self.hor_sign, self.cam = sun, rot, horizon, hor_sign, cam
        self._img_key = None
        self.update()

    # ---------- геометрия ----------
    def _basis(self):
        ya, pi = math.radians(self.yaw), math.radians(self.pitch)
        f = (-math.cos(pi) * math.sin(ya), -math.cos(pi) * math.cos(ya), -math.sin(pi))   # от глаза к центру
        rx, ry = f[1], -f[0]                           # f x Z, нормировано
        n = math.hypot(rx, ry) or 1.0
        r = (rx / n, ry / n, 0.0)
        u = (r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0])
        u = (-u[0], -u[1], -u[2]) if u[2] < 0 else u
        return f, r, u

    @staticmethod
    def _dir(az, el):
        a, e = math.radians(az), math.radians(el)
        return (math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e))

    def _project(self, p, basis, R, c):
        f, r, u = basis
        x = p[0] * r[0] + p[1] * r[1] + p[2] * r[2]
        y = p[0] * u[0] + p[1] * u[1] + p[2] * u[2]
        depth = -(p[0] * f[0] + p[1] * f[1] + p[2] * f[2])      # >0 — ближе к зрителю
        return c + x * R, c - y * R, depth

    # ---------- растр купола ----------
    def _render(self, S):
        f, r, u = self._basis()
        R, c = S / 2.0 - 1.0, S / 2.0
        buf = bytearray(S * S * 4)
        tex, tw, th = (self.tex, self.tw, self.th) if self.show_tex else (None, 0, 0)
        rot_t = self.rot / 360.0
        hz = self.hor_sign * self.horizon / 180.0
        fx, fy, fz = f
        atan2, asin, sqrt, pi = math.atan2, math.asin, math.sqrt, math.pi
        for j in range(S):
            sy = (c - (j + 0.5)) / R
            for i in range(S):
                sx = ((i + 0.5) - c) / R
                rr = sx * sx + sy * sy
                if rr >= 1.0:
                    continue
                t = sqrt(1.0 - rr)
                qx = sx * r[0] + sy * u[0]
                qy = sx * r[1] + sy * u[1]
                qz = sx * r[2] + sy * u[2]
                dx, dy, dz = qx + t * fx, qy + t * fy, qz + t * fz           # точка на дальней стороне купола
                if tex is not None:
                    uu = (-atan2(dx, dy) / (2 * pi) - rot_t) % 1.0
                    el = asin(dz if -1.0 < dz < 1.0 else (1.0 if dz > 0 else -1.0))
                    vv = 0.5 - (el / pi - hz)
                    tx = int(uu * tw) % tw
                    ty = min(th - 1, max(0, int(vv * th)))
                    k = (ty * tw + tx) * 3
                    cr, cg, cb = tex[k], tex[k + 1], tex[k + 2]
                else:
                    cr, cg, cb = 52, 58, 70
                sh = 0.62 + 0.38 * t                                          # края темнее — объём
                cr, cg, cb = cr * sh, cg * sh, cb * sh
                if fz:                                                        # земля (плоскость z = 0)
                    tg = -qz / fz
                    if -t <= tg <= t:
                        cr, cg, cb = cr * 0.42 + 36, cg * 0.42 + 38, cb * 0.42 + 42
                o = (j * S + i) * 4
                buf[o] = int(cb)
                buf[o + 1] = int(cg)
                buf[o + 2] = int(cr)
                buf[o + 3] = 255
        return QtGui.QImage(bytes(buf), S, S, S * 4, QtGui.QImage.Format_ARGB32).copy()

    # ---------- события ----------
    def _az_from_point(self, x, y):
        """Азимут, при котором точка (az, высота солнца) на экране ближе всего к клику."""
        el = self.sun[1] if self.sun is not None else 0.0
        el = max(0.0, el)
        W = self.SIZE
        R, c = W / 2.0 - 12.0, W / 2.0
        basis = self._basis()
        best, best_d = 0.0, 1e18
        for k in range(720):
            az = k * 0.5
            px, py, _d = self._project(self._dir(az, el), basis, R, c)
            d = (px - x) ** 2 + (py - y) ** 2
            if d < best_d:
                best, best_d = az, d
        lo = best
        for k in range(-10, 11):                       # уточнение до 0.05°
            az = (lo + k * 0.05) % 360.0
            px, py, _d = self._project(self._dir(az, el), basis, R, c)
            d = (px - x) ** 2 + (py - y) ** 2
            if d < best_d:
                best, best_d = az, d
        return best

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.RightButton:
            self.yaw, self.pitch = 200.0, 28.0
            self._drag, self._low = None, False
            self.update()
            return
        if e.button() == QtCore.Qt.LeftButton:
            self._drag = (e.position().x(), e.position().y(), self.yaw, self.pitch, False)
            self._low = True
            self.setCursor(QtCore.Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag:
            x0, y0, yaw0, pitch0, moved = self._drag
            dx, dy = e.position().x() - x0, e.position().y() - y0
            if not moved and dx * dx + dy * dy < 25.0:           # меньше 5 px — ещё клик
                return
            self._drag = (x0, y0, yaw0, pitch0, True)
            self.yaw = (yaw0 - dx * 0.6) % 360.0
            self.pitch = max(-80.0, min(85.0, pitch0 + dy * 0.4))
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag and e.button() == QtCore.Qt.LeftButton:
            x0, y0, _y, _p, moved = self._drag
            self._drag = None
            self._low = False
            self.setCursor(QtCore.Qt.OpenHandCursor)
            self.update()
            if not moved:
                self.clicked_az.emit(self._az_from_point(x0, y0))

    # ---------- рисование ----------
    def paintEvent(self, e):
        W = self.SIZE
        S = self.LO if self._low else self.HI
        key = (S, round(self.yaw, 1), round(self.pitch, 1), self.tex_key, round(self.rot, 2),
               round(self.horizon, 2), self.show_tex)
        if self._img_key != key:
            try:
                self._img = self._render(S)
            except Exception:
                self._img = None
                print("[SkyRig Studio] compass render error:\n" + traceback.format_exc())
            self._img_key = key
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.fillRect(0, 0, W, W, QtGui.QColor(38, 38, 38))
        R, c = W / 2.0 - 12.0, W / 2.0
        if self._img is not None:
            p.drawImage(QtCore.QRectF(c - R, c - R, 2 * R, 2 * R), self._img)
        basis = self._basis()
        pr = lambda pt: self._project(pt, basis, R, c)

        def line(points, color, width=1.0, alpha=255):
            pen = QtGui.QPen(QtGui.QColor(color[0], color[1], color[2], alpha))
            pen.setWidthF(width)
            p.setPen(pen)
            pts = [pr(q) for q in points]
            for a, b in zip(pts, pts[1:]):
                p.drawLine(QtCore.QPointF(a[0], a[1]), QtCore.QPointF(b[0], b[1]))

        p.setPen(QtGui.QPen(QtGui.QColor(150, 150, 150, 160), 1.0))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawEllipse(QtCore.QPointF(c, c), R, R)                         # контур сферы
        ring = [self._dir(a, 0.0) for a in range(0, 361, 6)]
        line(ring, (235, 235, 235), 1.6, 230)                              # горизонт
        for el in (30.0, 60.0):
            line([self._dir(a, el) for a in range(0, 361, 8)], (200, 200, 200), 0.8, 90)
        for az in (0, 90, 180, 270):                                       # меридианы
            line([self._dir(az, e2) for e2 in range(0, 91, 6)], (200, 200, 200), 0.8, 90)
        f = QtGui.QFont(self.font())
        f.setBold(True)
        f.setPointSizeF(9.0)
        p.setFont(f)
        for az, txt, col in ((0, "N", (255, 90, 90)), (90, "E", (220, 220, 220)),
                             (180, "S", (220, 220, 220)), (270, "W", (220, 220, 220))):
            x, y, _d = pr([v * 1.13 for v in self._dir(az, 0.0)])
            p.setPen(QtGui.QColor(*col))
            p.drawText(QtCore.QRectF(x - 9, y - 9, 18, 18), QtCore.Qt.AlignCenter, txt)
        if self.cam is not None:                                            # сектор камеры на земле
            caz, fov = self.cam
            half = max(5.0, min(80.0, fov / 2.0))
            pts = [(c, c)] + [pr(self._dir(a, 0.0))[:2] for a in
                              [caz - half + (2 * half) * k / 12.0 for k in range(13)]]
            poly = QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in pts])
            p.setPen(QtGui.QPen(QtGui.QColor(90, 200, 255, 230), 1.2))
            p.setBrush(QtGui.QColor(90, 200, 255, 70))
            p.drawPolygon(poly)
            x, y, _d = pr([v * 1.06 for v in self._dir(caz, 0.0)])
            p.setBrush(QtGui.QColor(90, 200, 255))
            p.drawEllipse(QtCore.QPointF(x, y), 3.0, 3.0)
        if self.sun is not None:                                            # солнце
            saz, sel = self.sun
            sp = self._dir(saz, sel)
            x, y, depth = pr(sp)
            below = sel < 0
            col = QtGui.QColor(255, 215, 40) if not below else QtGui.QColor(150, 130, 60)
            pen = QtGui.QPen(QtGui.QColor(255, 225, 90, 200 if not below else 110), 1.4)
            pen.setStyle(QtCore.Qt.DashLine)
            p.setPen(pen)
            p.drawLine(QtCore.QPointF(c, c), QtCore.QPointF(x, y))          # луч к центру сцены
            gx, gy, _d = pr(self._dir(saz, 0.0))                             # проекция на горизонт
            pen2 = QtGui.QPen(QtGui.QColor(255, 225, 90, 110), 1.0)
            pen2.setStyle(QtCore.Qt.DotLine)
            p.setPen(pen2)
            p.drawLine(QtCore.QPointF(gx, gy), QtCore.QPointF(x, y))
            grad = QtGui.QRadialGradient(QtCore.QPointF(x, y), 13.0)
            grad.setColorAt(0.0, QtGui.QColor(255, 235, 120, 200 if not below else 90))
            grad.setColorAt(1.0, QtGui.QColor(255, 220, 60, 0))
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(grad)
            p.drawEllipse(QtCore.QPointF(x, y), 13.0, 13.0)
            p.setPen(QtGui.QPen(QtGui.QColor(30, 30, 30), 1.2))
            p.setBrush(col)
            p.drawEllipse(QtCore.QPointF(x, y), 5.0 if depth < 0 else 6.0, 5.0 if depth < 0 else 6.0)
        p.end()


class SafeEnvironment(QtWidgets.QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName(WINDOW_OBJ_NAME)
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(330, 860)
        self._loading = False            # защита от рекурсии при синхронизации полей
        self._active_preset = None       # имя загруженного/сохранённого пресета
        self._marker_uv = None           # (u, v) красного крестика на превью
        self._preview_base = None
        self._shown_key = None
        self._last_horizon = 0.0
        self._range_key = None
        self._range_stats = None
        self._hist = []              # состояния сцены, которыми управляет скрипт
        self._hist_labels = []
        self._hist_idx = -1
        self._hist_depth = 0
        self._hist_key = None
        self._hist_t = 0.0
        self._hist_scene = None
        self._restoring = False
        self._skipped_channels = []
        self._no_cam = False           # (путь HDRI, EV превью), для которых нарисовано превью
        self._last_enter_sync = 0.0
        self._busy = False
        self.lib = Library()
        self.presets = PresetStore()
        self.sun_cache = SunCache()
        self._build_ui()
        disable_default_buttons(self)
        self._refresh_presets()
        self.sync_from_scene()

    # ---------- общие утилиты ----------
    @staticmethod
    def _close(a, b, tol=1e-3):
        try:
            return abs(float(a) - float(b)) <= tol
        except (TypeError, ValueError):
            return a == b

    @staticmethod
    def _preset_norm(p):
        """Пресет любого формата -> значения в единицах окна (Saturation 100 = норма, метры)."""
        q = dict(p)
        fmt = p.get("fmt", 1)
        sat = float(p.get("saturation", 100.0 if fmt >= 2 else 0.0))
        q["saturation"] = sat + 100.0 if fmt < 2 else sat
        sun = p.get("sun")
        if sun:
            sun = dict(sun)
            if fmt < 3:
                for k in ("distance", "icon"):
                    if k in sun:
                        sun[k] = units_to_m(float(sun[k]))
            q["sun"] = sun
        return q

    def _preset_matches(self, name):
        saved = self.presets.presets.get(name)
        if not saved:
            return False
        a, b = self._preset_norm(saved), self._collect_preset()
        for k in ("rotation", "exposure", "gamma", "saturation"):
            if not self._close(a.get(k, 0.0), b.get(k, 0.0)):
                return False
        if not self._close(a.get("blur", BLUR_DEFAULT), b.get("blur", BLUR_DEFAULT)):   # старые пресеты: Blur = 1
            return False
        for k in ("horizon", "cam_ev", "refl_ev", "refr_ev"):                          # старые пресеты: 0
            if not self._close(a.get(k, 0.0), b.get(k, 0.0)):
                return False
        pa, pb = a.get("hdri", ""), b.get("hdri", "")
        if (path_key(pa) if pa else "") != (path_key(pb) if pb else ""):
            return False
        sa, sb = a.get("sun"), b.get("sun")
        if bool(sa) != bool(sb):
            return False
        if sa:
            for k, dflt in (("height", 35.0), ("azimuth", 180.0), ("intensity", 1.0), ("size", 1.0),
                            ("distance", SUN_DIST_DEFAULT), ("icon", ICON_SIZE_DEFAULT)):
                if not self._close(sa.get(k, dflt), sb.get(k, dflt), 0.02):
                    return False
            if bool(sa.get("on", True)) != bool(sb.get("on", True)):
                return False
        return True

    def _link_ok(self):
        """None — солнца нет; True/False — совпадает ли оно с самой яркой точкой HDRI."""
        s = find_sun()
        if s is None:
            return None
        path = self._current_path()
        if not path:
            return False
        c = self.sun_cache.get(path)
        if not c:
            return False
        _cc, bm, _o = get_chain()
        rot = float(bm.uAngle) if bm is not None else 0.0
        az, el = hdri_pixel_to_direction(c["u"], c["v"], rot, bm_horizon(bm) if bm is not None else 0.0)
        el = max(0.0, min(90.0, el))
        z = s.transform.row3
        cur_el, cur_az = angles_from_z((float(z.x), float(z.y), float(z.z)))
        d_az = abs(cur_az - az) % 360.0
        d_az = min(d_az, 360.0 - d_az)
        return d_az <= 0.6 and abs(cur_el - el) <= 0.6

    def _active_cam_heading(self):
        """(азимут, горизонтальный fov) активной камеры вида или None."""
        try:
            c = rt.viewport.getCamera()
            if c is None:
                return None
            z = c.transform.row3                       # камера смотрит вдоль -Z
            az = math.degrees(math.atan2(-float(z.x), -float(z.y))) % 360.0
            try:
                fov = float(c.fov)
            except Exception:
                fov = 40.0
            return az, fov
        except Exception:
            return None

    def _update_compass(self):
        if not hasattr(self, "compass"):
            return
        try:
            _cc, bm, _o = get_chain()
            png = None
            path = self._current_path()
            if path and os.path.isfile(path):
                p2 = png_path(path, self.lib.thumb_ev)
                png = p2 if os.path.isfile(p2) else None
            self.compass.set_texture(png)
            s = find_sun()
            sun = None
            if s is not None:
                z = s.transform.row3
                el, az = angles_from_z((float(z.x), float(z.y), float(z.z)))
                sun = (az, el)
            rot = float(bm.uAngle) if bm is not None else 0.0
            hor = bm_horizon(bm) if bm is not None else 0.0
            cam = self._active_cam_heading()
            self.compass.set_state(sun, rot, HORIZON_ELEV_SIGN * hor, 1.0, cam)
            txt = "Sun: azimuth %.1f°, elevation %.1f°" % (sun[0], sun[1]) if sun else "No sun in the scene"
            if cam:
                txt += "\nCamera: azimuth %.0f°" % cam[0]
                if sun:
                    d = abs(sun[0] - cam[0]) % 360.0
                    d = min(d, 360.0 - d)
                    txt += ", sun %s" % ("in front" if d < 60 else ("behind" if d > 120 else "to the side"))
            self.lbl_compass.setText(txt)
        except Exception:
            print("[SkyRig Studio] compass update error:\n" + traceback.format_exc())

    def _refresh_state(self):
        """Строка состояния: пресет (совпадает / изменён / не сохранён) и предупреждение о привязке."""
        self._update_compass()
        self._sync_hline()                     # вид могли переключить на другую камеру
        if not hasattr(self, "lbl_state") or not hasattr(self, "lbl_link"):
            return
        try:
            name = self._active_preset
            if name and name not in self.presets.presets:
                name = self._active_preset = None
            if not name:
                self.lbl_state.setStyleSheet("color: #e0b050;")
                self.lbl_state.setText("Preset: none selected. Current lighting settings are not saved to a preset.")
            elif self._preset_matches(name):
                self.lbl_state.setStyleSheet("color: #7fcf7f;")
                self.lbl_state.setText("Preset: “%s”. Settings match the saved preset." % name)
            else:
                self.lbl_state.setStyleSheet("color: #e0b050;")
                self.lbl_state.setText("Preset: “%s”. Settings were changed and not saved "
                                       "(“Update” overwrites it)." % name)
            ok = self._link_ok()
            uv = None
            if ok:
                c = self.sun_cache.get(self._current_path())
                if c:
                    uv = (round(c["u"], 5), round(c["v"], 5))
            self._set_marker(uv)
            if ok is None:
                self.lbl_link.setText("")
            elif ok:
                self.lbl_link.setStyleSheet("")
                self.lbl_link.setText("The sun is aligned with the brightest point of the HDRI.")
            else:
                self.lbl_link.setStyleSheet("color: #ff4040; font-weight: bold;")
                self.lbl_link.setText("The sun is not linked to the brightest point of the HDRI.")
        except Exception:
            print("[SkyRig Studio] state error:\n" + traceback.format_exc())

    def _status(self, text, error=False):
        self.lbl_status.setStyleSheet("color: #ff7070;" if error else "")
        self.lbl_status.setText(text)
        self._refresh_state()

    def _run(self, title, fn):
        """Действие в undo-блоке; любая ошибка — сообщение в окне, а не падение Max."""
        t0 = time.time()
        alog("RUN  %s" % title)
        try:
            with pymxs.undo(True):
                result = fn()
            try:
                rt.redrawViews()
            except Exception:
                pass
            alog("DONE %s (%d ms)" % (title, (time.time() - t0) * 1000))
            return result
        except Exception:
            alog("FAIL %s: %s" % (title, traceback.format_exc().strip().splitlines()[-1]))
            print("[SkyRig Studio] %s:\n%s" % (title, traceback.format_exc()))
            self._status("Error: %s (details in Listener)" % title, error=True)
            return None

    # ---------- обновление скрипта ----------
    def _refresh_update_ui(self):
        latest = self.lib.update_latest
        if latest and ver_tuple(latest) > ver_tuple(VERSION):
            self.b_update.setText("Update to v%s" % latest)
            self.b_update.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold;")
        else:
            self.b_update.setText("Check for updates")
            self.b_update.setStyleSheet("")

    def _save_lib(self):
        try:
            self.lib.save()
        except Exception:
            print("[SkyRig Studio] library save error:\n" + traceback.format_exc())

    def auto_check_updates(self):
        """При запуске: раз в сутки тихая проверка (короткий таймаут); иначе показать уже известную новую версию."""
        self._refresh_update_ui()
        if not self.lib.update_auto or "/OWNER/" in update_manifest_url():
            return
        if time.time() - self.lib.update_checked < UPDATE_CHECK_SECONDS:
            return
        self.check_updates(silent=True)

    def _update_error_box(self, title, err):
        """Сообщение об ошибке обновления (строка состояния внизу окна легко остаётся незамеченной)."""
        try:
            code = getattr(err, "code", None)
            if code == 404:
                why = "Update page not found (error 404). Try again later or contact the script author."
            elif code:
                why = "The server returned error %s." % code
            else:
                why = "No connection to the update server. Check your internet connection."
            QtWidgets.QMessageBox.warning(self, "Updates", "%s.\n\n%s\n\nDetails: %s" % (title, why, err))
        except Exception:
            print("[SkyRig Studio] update error box failed:\n" + traceback.format_exc())

    def check_updates(self, silent=False):
        """Спрашивает манифест на сайте. Возвращает манифест (если есть новая версия) или None."""
        self.lib.update_checked = time.time()
        man, err = None, None
        self._wait_cursor(True) if not silent else None
        try:
            man = update_fetch_manifest(2.5 if silent else 8)
        except Exception as ex:
            err = ex
        finally:
            if not silent:
                self._wait_cursor(False)
        if err is not None:
            self._save_lib()
            if not silent:
                self._status("Could not check for updates: %s" % err, error=True)
                self._update_error_box("Could not check for updates", err)
            return None
        newer = ver_tuple(man["version"]) > ver_tuple(VERSION)
        self.lib.update_latest = man["version"] if newer else ""
        self.lib.update_notes = man["notes"] if newer else ""
        self._save_lib()
        self._refresh_update_ui()
        if not newer and not silent:
            self._status("You have the latest version (v%s)." % VERSION)
            QtWidgets.QMessageBox.information(self, "Updates", "You have the latest version: v%s." % VERSION)
        elif newer and silent:
            self._status("A new version v%s is available: see the button at the top." % man["version"])
        return man if newer else None

    def _on_update_button(self):
        if self._busy:
            return
        latest = self.lib.update_latest
        man = None
        if latest and ver_tuple(latest) > ver_tuple(VERSION):
            try:                                    # актуальный манифест: ссылка и контрольная сумма берутся свежими
                man = update_fetch_manifest(8)
                if ver_tuple(man["version"]) <= ver_tuple(VERSION):
                    man = None
            except Exception as ex:
                self._status("Could not get the update: %s" % ex, error=True)
                self._update_error_box("Could not get the update", ex)
                return
        if man is None:
            man = self.check_updates(silent=False)
            if man is None:
                return
        self._install_update(man)

    def _ask(self, title, text, yes_text, no_text, info="", icon=None, default_yes=True):
        """Окно с двумя кнопками. True — нажата первая («да»), False — вторая или закрытие окна."""
        box = QtWidgets.QMessageBox(self)
        box.setIcon(icon or QtWidgets.QMessageBox.Question)
        box.setWindowTitle(title)
        box.setText(text)
        if info:
            box.setInformativeText(info)
        b_yes = box.addButton(yes_text, QtWidgets.QMessageBox.AcceptRole)
        b_no = box.addButton(no_text, QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(b_yes if default_yes else b_no)
        box.setEscapeButton(b_no)
        box.exec()
        return box.clickedButton() is b_yes

    def _install_update(self, man):
        path = script_path()
        if not path:
            self._status("The script was not started from a file, so auto-update is unavailable. Download the new version manually.", error=True)
            return
        notes = man.get("notes") or "—"
        if not self._ask(
                "Update available",
                "A new version of SkyRig Studio is available: v%s\nInstalled version: v%s" % (man["version"], VERSION),
                "Update", "Cancel",
                info="What's new:\n%s\n\nAfter the update the script window will restart. The previous version is kept, "
                     "and you can go back to it (“▾” menu)." % notes,
                icon=QtWidgets.QMessageBox.Information):
            self._status("Update cancelled. The “Update to v%s” button stays at the top." % man["version"])
            return
        self._wait_cursor(True)
        try:
            data = update_download(man)
            update_backup(path)
            update_write(path, data)
        except PermissionError:
            self._wait_cursor(False)
            self._status("No write permission for %s: the update was not installed." % path, error=True)
            return
        except Exception as ex:
            self._wait_cursor(False)
            print("[SkyRig Studio] update error:\n" + traceback.format_exc())
            self._status("Update not installed: %s" % ex, error=True)
            return
        self._wait_cursor(False)
        self.lib.update_latest, self.lib.update_notes = "", ""
        self._save_lib()
        self._status("v%s installed, restarting the window..." % man["version"])
        QtCore.QTimer.singleShot(50, lambda p=path, v=VERSION: reload_script(p, v))

    def _show_update_menu(self):
        menu = QtWidgets.QMenu(self)
        act_auto = menu.addAction("Check on startup (once a day)")
        act_auto.setCheckable(True)
        act_auto.setChecked(self.lib.update_auto)
        backs = [(v, p) for v, p in update_backups() if v != VERSION]
        act_back = menu.addAction("Restore previous version (%s)" % backs[0][0] if backs else "Restore previous version")
        act_back.setEnabled(bool(backs))
        act_notes = menu.addAction("What's new in the available version")
        act_notes.setEnabled(bool(self.lib.update_notes))
        act_page = menu.addAction("Project page on GitHub")
        act_log = menu.addAction("Open action log")
        act = menu.exec(self.b_update_menu.mapToGlobal(QtCore.QPoint(0, self.b_update_menu.height())))
        if act is None:
            return
        if act is act_auto:
            self.lib.update_auto = act_auto.isChecked()
            self._save_lib()
        elif act is act_back:
            self._rollback(backs[0])
        elif act is act_notes:
            QtWidgets.QMessageBox.information(self, WINDOW_TITLE, "Version %s:\n\n%s" % (self.lib.update_latest, self.lib.update_notes))
        elif act is act_log:
            try:
                if not os.path.isfile(ACTION_LOG):
                    alog("log created")
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(ACTION_LOG))
            except Exception:
                pass
        elif act is act_page:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(UPDATE_PROJECT_PAGE))

    def _rollback(self, item):
        ver, bpath = item
        path = script_path()
        if not path:
            self._status("The script was not started from a file, so rollback is unavailable.", error=True)
            return
        if not self._ask("Restore previous version", "Restore version v%s?" % ver, "Restore", "Cancel",
                         info="The current version (v%s) will be kept in backups." % VERSION, default_yes=False):
            return
        try:
            with open(bpath, "rb") as fh:
                data = fh.read()
            compile(data.decode("utf-8-sig"), bpath, "exec")
            update_backup(path)
            update_write(path, data)
        except Exception as ex:
            print("[SkyRig Studio] rollback error:\n" + traceback.format_exc())
            self._status("Rollback failed: %s" % ex, error=True)
            return
        self.lib.update_latest, self.lib.update_notes = "", ""
        self.lib.update_checked = time.time()                      # чтобы автопроверка не вернула новую версию сразу
        self._save_lib()
        self._status("Going back to v%s, restarting the window..." % ver)
        QtCore.QTimer.singleShot(50, lambda p=path, v=VERSION: reload_script(p, v))

    def _wait_cursor(self, on):
        if on:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            QtWidgets.QApplication.processEvents()
        else:
            QtWidgets.QApplication.restoreOverrideCursor()

    # ---------- интерфейс ----------
    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # --- название скрипта и версия ---
        self.lbl_title = QtWidgets.QLabel("SkyRig Studio  v" + VERSION)
        self.lbl_title.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_title.setStyleSheet("font-weight: bold; font-size: 11pt; color: #f0f0f0; "
                                     "padding: 4px; border-bottom: 2px solid %s;" % UI_ACCENT)
        root.addWidget(self.lbl_title)

        # --- режим интерфейса: Просто / Расширенный ---
        mr = QtWidgets.QHBoxLayout()
        mr.setSpacing(0)
        self.b_mode_simple = QtWidgets.QPushButton("Simple")
        self.b_mode_adv = QtWidgets.QPushButton("Expert")
        for b_ in (self.b_mode_simple, self.b_mode_adv):
            b_.setCheckable(True)
            b_.setProperty("seg", True)
        self.b_mode_simple.setToolTip("Essentials only: preview, rotation, exposure, sun and presets.")
        self.b_mode_adv.setToolTip("All parameters: Gamma, Saturation, Blur, Horizon, EV channels, sun sizes, compass.")
        self.b_mode_simple.clicked.connect(lambda: self._set_simple_mode(True))
        self.b_mode_adv.clicked.connect(lambda: self._set_simple_mode(False))
        mr.addWidget(self.b_mode_simple)
        mr.addWidget(self.b_mode_adv)
        root.addLayout(mr)

        # --- обновления ---
        ur = QtWidgets.QHBoxLayout()
        ur.setSpacing(2)
        self.b_update = QtWidgets.QPushButton("Check for updates")
        self.b_update.setToolTip("Checks whether a new version of the script is available on GitHub. An update is installed only "
                                 "after your confirmation; the previous version is kept for rollback.")
        self.b_update.clicked.connect(self._on_update_button)
        self.b_update_menu = QtWidgets.QPushButton("▾")
        self.b_update_menu.setFixedWidth(26)
        self.b_update_menu.setToolTip("Update settings")
        self.b_update_menu.clicked.connect(self._show_update_menu)
        ur.addWidget(self.b_update, 1)
        ur.addWidget(self.b_update_menu)
        root.addLayout(ur)

        # --- превью ---
        self.lbl_preview = ClickableLabel("No HDRI\n(click to open the library)")
        self.lbl_preview.setFixedSize(PREVIEW_W, PREVIEW_H)
        self.lbl_preview.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_preview.setCursor(QtCore.Qt.PointingHandCursor)
        self.lbl_preview.setStyleSheet("border: 1px solid #555; border-radius: 6px; background: #222;")
        self.lbl_preview.setToolTip("Click to open the HDRI library")
        self.lbl_preview.clicked.connect(self.open_library)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.lbl_preview)
        row.addStretch(1)
        root.addLayout(row)
        self.lbl_current = QtWidgets.QLabel("—")
        self.lbl_current.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_current.setWordWrap(True)
        root.addWidget(self.lbl_current)

        row_nav = QtWidgets.QHBoxLayout()
        b_prev = QtWidgets.QPushButton("Prev")
        set_btn_icon(b_prev, "prev")
        b_prev.setToolTip("Previous HDRI in the group selected in the library (wraps around). If linking is on, the sun is placed on its bright point.")
        b_prev.clicked.connect(lambda: self.step_hdri(-1))
        b_next = QtWidgets.QPushButton("Next")
        set_btn_icon(b_next, "next")
        b_next.setLayoutDirection(QtCore.Qt.RightToLeft)
        b_next.setToolTip("Next HDRI in the group selected in the library (wraps around). If linking is on, the sun is placed on its bright point.")
        b_next.clicked.connect(lambda: self.step_hdri(1))
        row_nav.addWidget(b_prev)
        row_nav.addWidget(b_next)
        root.addLayout(row_nav)
        row = QtWidgets.QHBoxLayout()
        b_lib = QtWidgets.QPushButton("Library...")
        set_btn_icon(b_lib, "library")
        b_lib.clicked.connect(self.open_library)
        b_reload = QtWidgets.QPushButton("Reload HDR")
        set_btn_icon(b_reload, "reload")
        b_reload.clicked.connect(self.reload_hdr)
        row.addWidget(b_lib)
        row.addWidget(b_reload)
        root.addLayout(row)
        b_ir = QtWidgets.QPushButton("Start Interactive Render")
        b_ir.setMinimumHeight(30)
        b_ir.setToolTip("Starts Corona Interactive Rendering in the active viewport.")
        b_ir.clicked.connect(self.start_interactive)
        mark_primary(b_ir)
        set_btn_icon(b_ir, "play", "#1e1e1e")
        root.addWidget(b_ir)

        # --- настройки HDRI ---
        g = self._card("HDRI settings", "hdri")
        f = QtWidgets.QFormLayout(g.body)
        f.setContentsMargins(0, 2, 0, 0)
        self.sp_rot = make_spin(-100000, 100000, 1, 0, "°", default=0.0, pixel_divider=1.0)   # целые градусы, 1°/пиксель
        self.sl_rot = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sl_rot.setRange(0, 360)               # 0° — левый край, 360° — правый
        self.sl_rot.setTracking(False)           # применять при отпускании, а не на каждый пиксель
        self.sp_exp = make_spin(-10, 10, 0.1, 2, " EV", default=0.0)
        self.sp_gamma = make_spin(0.1, 5, 0.05, 2, default=1.0)
        self.sp_sat = make_spin(0, 200, 5, 1, " %", default=100.0)   # 100 % = без изменений
        self.sp_blur = make_spin(BLUR_MIN, BLUR_MAX, 0.1, 2, default=BLUR_DEFAULT, pixel_divider=0.1)   # Blur у CoronaBitmap
        self.sp_horizon = make_spin(HORIZON_MIN, HORIZON_MAX, 0.1, 2, "°", default=0.0, pixel_divider=0.1)
        self.sp_horizon.setToolTip("Vertical shift of the HDRI horizon (Offset V of CoronaBitmap).")
        self.sp_cam = make_spin(-10, 10, 0.1, 2, " EV", default=0.0)
        self.sp_cam.setToolTip("Brightness of the background the camera sees, relative to the main exposure. 0 = no separate adjustment.")
        self.sp_refl = make_spin(-10, 10, 0.1, 2, " EV", default=0.0)
        self.sp_refl.setToolTip("Brightness of the HDRI in reflections, relative to the main exposure. 0 = no separate adjustment.")
        self.sp_refr = make_spin(-10, 10, 0.1, 2, " EV", default=0.0)
        self.sp_refr.setToolTip("Brightness of the HDRI in refractions, relative to the main exposure. 0 = no separate adjustment.")
        self.sp_rot.valueChanged.connect(self._on_rot_spin)
        self.sl_rot.valueChanged.connect(self._on_rot_slider)
        for s in (self.sp_exp, self.sp_gamma, self.sp_sat, self.sp_blur, self.sp_horizon,
                  self.sp_cam, self.sp_refl, self.sp_refr):
            s.valueChanged.connect(self._on_hdri_setting)
        f.addRow("Rotation", self.sp_rot)
        f.addRow("", self.sl_rot)
        row_step = QtWidgets.QHBoxLayout()
        b_rm = QtWidgets.QPushButton("−5°")
        b_rm.setToolTip("Rotate the HDRI by 5° in the negative direction")
        b_rm.clicked.connect(lambda: self.sp_rot.setValue(self.sp_rot.value() - 5.0))
        b_rp = QtWidgets.QPushButton("+5°")
        b_rp.setToolTip("Rotate the HDRI by 5° in the positive direction")
        b_rp.clicked.connect(lambda: self.sp_rot.setValue(self.sp_rot.value() + 5.0))
        row_step.addWidget(b_rm)
        row_step.addWidget(b_rp)
        f.addRow("", row_step)
        f.addRow("Exposure", self.sp_exp)
        f.addRow("Gamma", self.sp_gamma)
        f.addRow("Saturation", self.sp_sat)
        self.sp_blur.setFixedWidth(64)               # поле Blur короткое
        b_blur_min = QtWidgets.QPushButton("min")
        b_blur_min.setFixedWidth(34)
        b_blur_min.setToolTip("Blur = %s" % BLUR_MIN)
        b_blur_min.clicked.connect(lambda: self.sp_blur.setValue(BLUR_MIN))
        row_blur = QtWidgets.QHBoxLayout()
        row_blur.setContentsMargins(0, 0, 0, 0)
        row_blur.addWidget(b_blur_min)
        row_blur.addWidget(self.sp_blur)
        row_blur.addStretch(1)
        f.addRow("Blur", row_blur)
        f.addRow("Horizon", self.sp_horizon)
        self.chk_hline = ToggleSwitch("Horizon line")
        self.chk_hline.setToolTip("Shows the horizon line (eye level) in the camera of the current view — the camera's own "
                                  "“Show Horizon” option, drawn over the geometry. With Horizon = 0 the HDRI horizon lies "
                                  "exactly on this line.")
        self.chk_hline.toggled.connect(self._on_hline)
        f.addRow("", self.chk_hline)
        self.cmb_target = TargetCombo(self._refresh_target_label)
        self.cmb_target.addItem("Corona global settings", TARGET_RENDERER)
        self.cmb_target.addItem("Active CoronaCam", TARGET_CAMERA)
        self._refresh_target_label()
        self.cmb_target.setCurrentIndex(1 if self.lib.channel_target == TARGET_CAMERA else 0)
        self.cmb_target.setToolTip("Where to write Direct / Reflect / Refract EV: to the Scene Environment of the global Corona settings "
                                   "or to the Environment override of the active (in the current view) CoronaCam.")
        self.cmb_target.currentIndexChanged.connect(self._on_target_changed)
        f.addRow("EV channels in", self.cmb_target)
        f.addRow("Direct EV", self.sp_cam)
        f.addRow("Reflect EV", self.sp_refl)
        f.addRow("Refract EV", self.sp_refr)
        b_reset = QtWidgets.QPushButton("Reset HDRI settings")
        set_btn_icon(b_reset, "reset")
        b_reset.clicked.connect(self.reset_hdri_settings)
        f.addRow(b_reset)
        root.addWidget(g)

        # --- солнце ---
        gs = self._card("Sun (Corona Sun)", "sun")
        sl = QtWidgets.QVBoxLayout(gs.body)
        sl.setContentsMargins(0, 2, 0, 0)
        self.lbl_sun = QtWidgets.QLabel("Sun: not found")
        sl.addWidget(self.lbl_sun)
        row = QtWidgets.QHBoxLayout()
        b_create = QtWidgets.QPushButton("Create at center")
        set_btn_icon(b_create, "sun")
        b_create.clicked.connect(self.create_sun)
        b_select = QtWidgets.QPushButton("Select")
        b_select.clicked.connect(self.select_sun)
        self.chk_on = ToggleSwitch("On")
        self.chk_on.toggled.connect(self._on_sun_setting)
        row.addWidget(b_create)
        row.addWidget(b_select)
        row.addWidget(self.chk_on)
        sl.addLayout(row)
        fs = QtWidgets.QFormLayout()
        self.sp_elev = make_spin(0, 90, 1, 2, "°", default=35.0)
        self.sp_azim = make_spin(0, 360, 5, 2, "°", default=180.0)
        self.sp_int = make_spin(0, 1000, 0.1, 2, default=1.0)
        self.sp_size = make_spin(0.01, 1000, 0.1, 2, default=1.0)
        self.sp_dist = make_spin(SUN_DIST_MIN, SUN_DIST_MAX, 1, 0, "", default=SUN_DIST_DEFAULT, pixel_divider=1.0)
        self.sp_icon = make_spin(0.1, 100, 1, 1, "", default=ICON_SIZE_DEFAULT, pixel_divider=5.0)
        self.sp_elev.valueChanged.connect(self._on_sun_setting)
        self.sp_azim.valueChanged.connect(self._on_azimuth_manual)
        self.sp_int.valueChanged.connect(self._on_sun_setting)
        self.sp_size.valueChanged.connect(self._on_sun_setting)
        self.sp_dist.valueChanged.connect(self._on_sun_setting)
        self.sp_icon.valueChanged.connect(self._on_sun_setting)
        self.sp_elev.setToolTip("Sun elevation above the horizon, degrees")
        fs.addRow("Height", self.sp_elev)
        self.sp_azim.setToolTip("Azimuth: 0 = north (+Y), clockwise")
        fs.addRow("Azimuth", self.sp_azim)
        fs.addRow("Intensity", self.sp_int)
        fs.addRow("Size", self.sp_size)
        self.sp_dist.setToolTip("Distance from the target (0, 0, 0) to the sun, meters")
        fs.addRow("Distance", self.sp_dist)
        self.sp_icon.setToolTip("Size of the sun icon in the viewport, meters")
        fs.addRow("Icon", self.sp_icon)
        sl.addLayout(fs)
        b_align = QtWidgets.QPushButton("link to HDRI")
        b_align.setToolTip("Finds the brightest point of the current HDRI and places the sun in the same "
                           "direction, taking Rotation into account.")
        b_align.clicked.connect(self.align_sun)
        mark_primary(b_align)
        set_btn_icon(b_align, "link", "#1e1e1e")
        sl.addWidget(b_align)
        self.prog_find = QtWidgets.QProgressBar()
        self.prog_find.setRange(0, 100)
        self.prog_find.setTextVisible(True)
        self.prog_find.setAlignment(QtCore.Qt.AlignCenter)
        self.prog_find.setVisible(False)
        sl.addWidget(self.prog_find)
        self.chk_link = ToggleSwitch("Link azimuth to HDRI")
        self.chk_link.setToolTip("Follow Rotation. While on, rotating the HDRI recalculates the sun azimuth "
                                 "so that it stays on the bright point of the map.\n"
                                 "Off by default; turns on by itself after the “link to HDRI” button.")
        self.chk_link.setChecked(False)                    # по умолчанию выключено; кнопка «link to HDRI» включает
        self.chk_link.toggled.connect(lambda *_: self._refresh_state())
        sl.addWidget(self.chk_link)
        root.addWidget(gs)

        # --- пресеты ---
        gp = self._card("Lighting presets", "presets")
        pl = QtWidgets.QVBoxLayout(gp.body)
        pl.setContentsMargins(0, 2, 0, 0)
        self.cmb_presets = QtWidgets.QComboBox()
        pl.addWidget(self.cmb_presets)
        row = QtWidgets.QHBoxLayout()
        b_pload = QtWidgets.QPushButton("Load")
        b_pload.clicked.connect(self.load_preset)
        b_psave = QtWidgets.QPushButton("Save as...")
        b_psave.clicked.connect(self.save_preset_as)
        b_pupd = QtWidgets.QPushButton("Update")
        b_pupd.setToolTip("Overwrite the selected preset with the current parameters")
        b_pupd.clicked.connect(self.update_preset)
        b_pdel = QtWidgets.QPushButton("Delete")
        set_btn_icon(b_pdel, "trash")
        b_pdel.clicked.connect(self.delete_preset)
        grid = QtWidgets.QGridLayout()          # 2x2, чтобы панель оставалась узкой
        grid.addWidget(b_pload, 0, 0)
        grid.addWidget(b_psave, 0, 1)
        grid.addWidget(b_pupd, 1, 0)
        grid.addWidget(b_pdel, 1, 1)
        b_pexp = QtWidgets.QPushButton("To file...")
        b_pexp.setToolTip("Save presets to a file to move them to another scene or computer.")
        b_pexp.clicked.connect(self.export_presets)
        b_pimp = QtWidgets.QPushButton("From file...")
        b_pimp.setToolTip("Load presets from a file saved with the “To file...” button.")
        b_pimp.clicked.connect(self.import_presets)
        grid.addWidget(b_pexp, 2, 0)
        grid.addWidget(b_pimp, 2, 1)
        b_pscene = QtWidgets.QPushButton("Save next to scene")
        b_pscene.setToolTip("No dialogs: all presets are saved to the folder of the open scene in the file\n"
                            "“SceneName.presets.json”. Pressing again overwrites this file.\n"
                            "To load back: “From file...”.")
        b_pscene.clicked.connect(self.save_presets_beside_scene)
        grid.addWidget(b_pscene, 3, 0, 1, 2)
        pl.addLayout(grid)
        root.addWidget(gp)

        self.lbl_state = QtWidgets.QLabel("")
        self.lbl_state.setWordWrap(True)
        root.addWidget(self.lbl_state)
        self.lbl_link = QtWidgets.QLabel("")
        self.lbl_link.setWordWrap(True)
        root.addWidget(self.lbl_link)
        self.lbl_status = QtWidgets.QLabel("")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        gc = self._card("Sun compass", "compass")
        self._card_compass = gc
        cl = QtWidgets.QVBoxLayout(gc.body)
        cl.setContentsMargins(0, 2, 0, 0)
        self.compass = SunCompass()
        crow = QtWidgets.QHBoxLayout()
        crow.addStretch(1)
        crow.addWidget(self.compass)
        crow.addStretch(1)
        cl.addLayout(crow)
        self.chk_compass_tex = ToggleSwitch("Show HDRI texture")
        self.chk_compass_tex.setChecked(True)
        self.chk_compass_tex.setToolTip("Turn off to get a plain sphere (easier to see the lines and the sun).")
        self.chk_compass_tex.toggled.connect(self.compass.set_show_tex)
        cl.addWidget(self.chk_compass_tex)
        self.compass.clicked_az.connect(self._on_compass_click)
        self.lbl_compass = QtWidgets.QLabel("")
        self.lbl_compass.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_compass.setWordWrap(True)
        self.lbl_compass.setStyleSheet("color: #b0b0b0; font-size: 8pt;")
        cl.addWidget(self.lbl_compass)
        root.addWidget(gc)
        root.addStretch(1)

        row_h = QtWidgets.QHBoxLayout()
        self.b_undo = QtWidgets.QPushButton("Undo")
        set_btn_icon(self.b_undo, "undo")
        self.b_undo.clicked.connect(self.hist_undo)
        self.b_redo = QtWidgets.QPushButton("Redo")
        set_btn_icon(self.b_redo, "redo")
        self.b_redo.setLayoutDirection(QtCore.Qt.RightToLeft)
        self.b_redo.clicked.connect(self.hist_redo)
        row_h.addWidget(self.b_undo)
        row_h.addWidget(self.b_redo)
        root.addLayout(row_h)
        self._hist_buttons()

        b_all = QtWidgets.QPushButton("Reset all values")
        b_all.setMinimumHeight(30)
        b_all.setToolTip("Removes the sun (with target and icon), clears the Single map slot (Scene Environment) "
                         "and returns all parameters to defaults.")
        b_all.clicked.connect(self.reset_all)
        set_btn_icon(b_all, "reset")
        root.addWidget(b_all)

        self.lbl_copyright = QtWidgets.QLabel(COPYRIGHT_TEXT)
        self.lbl_copyright.setWordWrap(True)
        self.lbl_copyright.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_copyright.setStyleSheet("color: #8a8a8a; font-size: 8pt;")
        root.addWidget(self.lbl_copyright)
        self.lbl_mail = QtWidgets.QLabel('<a href="mailto:%s" style="color:#8a8a8a;">%s</a>' % (CONTACT_EMAIL, CONTACT_EMAIL))
        self.lbl_mail.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl_mail.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction | QtCore.Qt.TextSelectableByMouse)
        self.lbl_mail.setOpenExternalLinks(True)
        self.lbl_mail.setStyleSheet("color: #8a8a8a; font-size: 8pt;")
        root.addWidget(self.lbl_mail)
        if DONATE_URL:
            bd = QtWidgets.QPushButton(DONATE_TEXT_BTN)
            bd.setMinimumHeight(28)
            bd.setProperty("primary", True)
            bd.setCursor(QtCore.Qt.PointingHandCursor)
            bd.setToolTip("Support the author via PayPal (opens in the browser)")
            bd.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(DONATE_URL)))
            root.addWidget(bd)
        try:
            self.setStyleSheet(UI_QSS)                      # оформление v0.52 (только это окно)
        except Exception:
            pass
        # элементы, которые прячутся в режиме «Просто» (значения при этом сохраняются и продолжают действовать)
        self._adv_items = [(f, self.sp_gamma), (f, self.sp_sat), (f, row_blur), (f, self.sp_horizon),
                           (f, self.cmb_target), (f, self.sp_cam), (f, self.sp_refl), (f, self.sp_refr),
                           (fs, self.sp_size), (fs, self.sp_dist), (fs, self.sp_icon)]
        self._adv_widgets = [b_pexp, b_pimp, b_pscene, self._card_compass]
        self._apply_mode()

    def _card(self, title, key):
        card = CollapsibleCard(title, key, collapsed=(key in self.lib.ui_collapsed))
        card.state_changed.connect(self._on_card_toggled)
        return card

    def _on_card_toggled(self, key, collapsed):
        try:
            cur = [k for k in self.lib.ui_collapsed if k != key]
            if collapsed:
                cur.append(key)
            self.lib.ui_collapsed = cur
            self._save_lib()
        except Exception:
            pass

    def _set_simple_mode(self, simple):
        self.lib.ui_simple = bool(simple)
        self._save_lib()
        self._apply_mode()

    def _apply_mode(self):
        """Режим «Просто» только прячет редкие элементы; параметры и их значения не меняются."""
        simple = bool(self.lib.ui_simple)
        try:
            self.b_mode_simple.setChecked(simple)
            self.b_mode_adv.setChecked(not simple)
            for form, item in self._adv_items:
                form.setRowVisible(item, not simple)
            for w in self._adv_widgets:
                w.setVisible(not simple)
        except Exception:
            print("[SkyRig Studio] apply_mode error:\n" + traceback.format_exc())

    @undoable("HDRI change")
    def step_hdri(self, direction):
        """Следующая / предыдущая HDRI в группе, которая выбрана в библиотеке (по кругу)."""
        group = self.lib.last_group or ALL_GROUP
        files = self.lib.files_of(group)
        if not files:
            self._status("There are no files in group “%s”: select a group in the library." % group, error=True)
            return
        cur = path_key(self._current_path()) if self._current_path() else None
        keys = [path_key(p) for p in files]
        n = len(files)
        if cur in keys:
            i = keys.index(cur)
        else:
            i = -1 if direction > 0 else 0
        for _ in range(n):
            i = (i + direction) % n
            if os.path.isfile(files[i]):
                if self.apply_path(files[i]):
                    self._status("%s  [%d/%d, group “%s”]" % (os.path.basename(files[i]), i + 1, n, group))
                return
        self._status("There are no available files in group “%s”." % group, error=True)

    # ---------- собственная история изменений (не связана с Ctrl+Z в 3ds Max) ----------
    def _scene_state(self):
        """Всё, чем управляет скрипт, читается прямо из сцены."""
        st = {"hdri": None, "sun": None}
        cc, bm, _o = get_chain()
        if bm is not None:
            ev, gm, sat = cc_read(cc) if cc is not None else (0.0, 1.0, 100.0)
            ev -= self._corr_ev(str(bm.filename))      # в истории — Exposure пользователя, без поправки
            tg = self.lib.channel_target
            st["hdri"] = dict(path=str(bm.filename), rotation=float(bm.uAngle), exposure=ev, gamma=gm,
                              saturation=sat, blur=float(bm.filteringBlur), horizon=bm_horizon(bm),
                              cam_ev=channel_ev(tg, *CHANNELS[0]), refl_ev=channel_ev(tg, *CHANNELS[1]),
                              refr_ev=channel_ev(tg, *CHANNELS[2]))
        s = find_sun()
        if s is not None:
            z = s.transform.row3
            el, az = angles_from_z((float(z.x), float(z.y), float(z.z)))
            ic = find_icon()
            st["sun"] = dict(on=bool(s.on), height=el, azimuth=az, intensity=float(s.intensity),
                             size=float(s.sizeMultiplier), distance=units_to_m(_sun_distance(s)),
                             icon=units_to_m(float(ic.size)) if ic is not None else ICON_SIZE_DEFAULT)
        return st

    def _scene_id(self):
        try:
            return (str(rt.maxFilePath), str(rt.maxFileName))
        except Exception:
            return None

    def _hist_begin(self):
        if self._restoring:
            return
        if self._hist_depth == 0:
            sid = self._scene_id()
            if self._hist and sid != self._hist_scene:       # открыта другая сцена: история не подходит
                self._hist, self._hist_labels, self._hist_idx = [], [], -1
            cur = self._scene_state()
            if not self._hist:
                self._hist, self._hist_labels, self._hist_idx = [cur], ["start"], 0
                self._hist_scene = sid
            elif not _state_equal(cur, self._hist[self._hist_idx]):
                # изменено вне скрипта: фиксируем как отдельный шаг, чтобы «Отменить» вернуло именно к нему
                del self._hist[self._hist_idx + 1:]
                del self._hist_labels[self._hist_idx + 1:]
                self._hist.append(cur)
                self._hist_labels.append("changed outside the script")
                self._hist_idx += 1
                self._hist_key = None
        self._hist_depth += 1

    def _hist_end(self, label):
        if self._restoring:
            return
        self._hist_depth -= 1
        if self._hist_depth > 0:
            return
        try:
            cur = self._scene_state()
        except Exception:
            return
        if self._hist and _state_equal(cur, self._hist[self._hist_idx]):
            return
        now = time.monotonic()
        last = len(self._hist) - 1
        if (label == self._hist_key and now - self._hist_t < HIST_MERGE_SEC
                and self._hist_idx == last and last >= 1):
            self._hist[last] = cur                          # продолжение той же протяжки — один шаг
        else:
            del self._hist[self._hist_idx + 1:]
            del self._hist_labels[self._hist_idx + 1:]
            self._hist.append(cur)
            self._hist_labels.append(label)
            if len(self._hist) > HIST_LIMIT:
                self._hist.pop(0)
                self._hist_labels.pop(0)
            self._hist_idx = len(self._hist) - 1
        self._hist_key, self._hist_t = label, now
        self._hist_buttons()

    def _hist_buttons(self):
        can_u = self._hist_idx > 0
        can_r = 0 <= self._hist_idx < len(self._hist) - 1
        self.b_undo.setEnabled(can_u)
        self.b_redo.setEnabled(can_r)
        self.b_undo.setToolTip("Undo: %s" % self._hist_labels[self._hist_idx] if can_u
                               else "Nothing to undo. This is the history of this script only; Ctrl+Z in 3ds Max is not affected.")
        self.b_redo.setToolTip("Redo: %s" % self._hist_labels[self._hist_idx + 1] if can_r
                               else "Nothing to redo.")

    def _hist_goto(self, new_idx, verb):
        if self._busy:
            return
        if self._hist and self._scene_id() != self._hist_scene:
            self._hist, self._hist_labels, self._hist_idx = [], [], -1
            self._hist_buttons()
            self._status("Another scene was opened: the script history was reset.")
            return
        if not (0 <= new_idx < len(self._hist)):
            return
        label = self._hist_labels[self._hist_idx if verb == "Undone" else new_idx]
        self._restoring = True
        try:
            ok = self._restore_state(self._hist[new_idx])
        finally:
            self._restoring = False
        self._hist_idx = new_idx
        self._hist_key = None
        self.sync_from_scene()
        self._hist_buttons()
        self._status("%s: %s" % (verb, label) if ok else "The state was not fully restored (see Listener).",
                     error=not ok)

    def hist_undo(self):
        self._hist_goto(self._hist_idx - 1, "Undone")

    def hist_redo(self):
        self._hist_goto(self._hist_idx + 1, "Redone")

    def _set_hdri_file(self, path):
        """Кладёт HDRI в Single map без вопросов и без привязки солнца (для восстановления состояния)."""
        adopt_legacy()
        cc, bm, _o = get_chain()
        if bm is None:
            bm = rt.CoronaBitmap()
            bm.name = BM_NAME
            cc = None
        cc = ensure_cc(cc, bm)
        if cc_input(cc) is None or _class_name(cc_input(cc)) != "CoronaBitmap":
            cc_set_input(cc, bm)
        if str(bm.filename) != path:
            bm.filename = path
        set_single_map(cc)

    def _restore_state(self, st):
        ok = True
        try:
            with pymxs.undo(True):
                h = st.get("hdri")
                if h is None:
                    r = corona_renderer()
                    if r is not None:
                        clear_channels()
                        cur = r.bg_texmap
                        if cur is None or _our_chain(cur) is not None:
                            r.bg_texmap = None
                            r.bg_source = 0
                else:
                    if os.path.isfile(h["path"]):
                        self._set_hdri_file(h["path"])
                    else:
                        ok = False
                    self._write_hdri(h["rotation"], h["exposure"], h["gamma"], h["saturation"], h["blur"],
                                     h["horizon"], h["cam_ev"], h["refl_ev"], h["refr_ev"])
                sun = st.get("sun")
                if sun is None:
                    delete_sun_objects()
                else:
                    s = find_sun()
                    if s is None:
                        s = rt.CoronaSun()
                        s.name = SUN_NAME
                        s.pos = rt.point3(0, 0, 0)
                    set_sun_direction(s, sun["height"], sun["azimuth"], m_to_units(sun["distance"]))
                    ensure_icon(s, m_to_units(sun["icon"]))
                    s.intensity = sun["intensity"]
                    s.sizeMultiplier = sun["size"]
                    s.on = sun["on"]
            rt.redrawViews()
        except Exception:
            print("[SkyRig Studio] restore state:\n" + traceback.format_exc())
            ok = False
        return ok

    def start_interactive(self):
        """Запуск Corona Interactive Rendering (то же, что кнопка Interactive в Corona)."""
        if corona_renderer() is None:
            self._status("Current renderer is not Corona: interactive rendering is unavailable.", error=True)
            return
        try:
            alog("ACTION Start Interactive Render")
            rt.CoronaRenderer.CoronaFp.startInteractive()
            self._status("Corona Interactive Rendering started.")
        except Exception as ex:
            self._status("Could not start interactive rendering: %s" % ex, error=True)

    # ---------- превью / библиотека ----------
    def _current_path(self):
        try:
            _cc, bm, _o = get_chain()
            return str(bm.filename) if bm is not None else ""
        except Exception:
            return ""

    def _update_preview(self, path):
        pm = None
        try:
            if path and os.path.isfile(path):
                png = make_thumb(path, self.lib.thumb_ev)   # из кэша или создаёт один раз
                if png:
                    pm = QtGui.QPixmap(png)
        except Exception:
            print("[SkyRig Studio] preview error:\n" + traceback.format_exc())
        if pm is not None and not pm.isNull():
            self._preview_base = pm.scaled(PREVIEW_W, PREVIEW_H, QtCore.Qt.KeepAspectRatio,
                                           QtCore.Qt.SmoothTransformation)
        else:
            self._preview_base = None
            self.lbl_preview.setPixmap(QtGui.QPixmap())
            self.lbl_preview.setText("No preview" if path else "No HDRI\n(click to open the library)")
        self._draw_preview()

    def _draw_preview(self):
        """Превью + красный крестик в найденной яркой точке (если солнце совмещено с ней)."""
        base = getattr(self, "_preview_base", None)
        if base is None:
            return
        pm = QtGui.QPixmap(base)
        uv = getattr(self, "_marker_uv", None)
        if uv is not None:
            pw, ph = pm.width(), pm.height()
            x, y = uv[0] * pw, uv[1] * ph
            L = 7
            painter = QtGui.QPainter(pm)
            try:
                for color, width in ((QtGui.QColor(0, 0, 0, 200), 4), (QtGui.QColor(255, 30, 30), 2)):
                    painter.setPen(QtGui.QPen(color, width))
                    painter.drawLine(QtCore.QPointF(x - L, y), QtCore.QPointF(x + L, y))
                    painter.drawLine(QtCore.QPointF(x, y - L), QtCore.QPointF(x, y + L))
            finally:
                painter.end()
        self.lbl_preview.setPixmap(pm)

    def _set_marker(self, uv):
        if uv != getattr(self, "_marker_uv", None):
            self._marker_uv = uv
            self._draw_preview()

    def open_library(self):
        dlg = LibraryDialog(self, self.lib, self.apply_path, self._current_path())
        dlg.exec()
        try:
            self.lib.save()
        except Exception:
            pass
        self.sync_from_scene()

    # ---------- применение HDRI ----------
    @undoable("Apply HDRI")
    def apply_path(self, path, auto_exposure=True):
        """Ставит HDRI в Scene Environment -> Single map (Corona). Вызывается и окном библиотеки.
        auto_exposure: при смене карты Exposure сбрасывается на 0 (если включено выравнивание яркости —
        ставится по поправке карты). Повторное применение той же карты Exposure не трогает."""
        alog("  apply_path %s" % path)
        if not os.path.isfile(path):
            self._status("File not found: %s" % path, error=True)
            return False
        if corona_renderer() is None:
            self._status("Current renderer is not Corona: the Single map slot is unavailable.", error=True)
            return False
        cc, bm, other = get_chain()
        if other:
            Msg = QtWidgets.QMessageBox
            r = Msg.question(
                self, WINDOW_TITLE,
                "The Single map slot (Scene Environment) already holds a different map.\n"
                "Replace it with the HDRI?\n\nYou can undo with Ctrl+Z.",
                Msg.Yes | Msg.No, Msg.No)
            if r != Msg.Yes:
                return False

        prev_path = self._current_path()

        def do():
            adopt_legacy()
            cc2, bm2, _o = get_chain()
            if bm2 is None:
                bm2 = rt.CoronaBitmap()
                bm2.name = BM_NAME
                cc2 = None
            cc2 = ensure_cc(cc2, bm2)
            if cc_input(cc2) is None or _class_name(cc_input(cc2)) != "CoronaBitmap":
                cc_set_input(cc2, bm2)
            bm2.filename = path
            set_single_map(cc2)               # слот Single map + переключатель на «Single map»
            return True

        if not self._run("apply HDRI", do):
            return False
        self.sync_from_scene()
        sun_note = ""
        if find_sun() is not None:
            if self.chk_link.isChecked():
                self._align_impl(create_if_missing=False, quiet=True)
            elif getattr(self.lib, "sun_auto_align", True):
                # солнце сразу по новой карте: положение берётся из кэша («Detect sun…» в библиотеке) или ищется
                # один раз; карты без явного солнца (пасмурно, ночь) солнце не двигают
                try:
                    pt = self._hdri_sun_point(path)
                except Exception:
                    print("[SkyRig Studio] find brightest error:\n" + traceback.format_exc())
                    pt = None
                if pt is not None and pt[2] >= SUN_MIN_PEAK:
                    if self._align_impl(create_if_missing=False, quiet=True):
                        sun_note = " Sun aligned with the HDRI."
                elif pt is not None:
                    sun_note = " No distinct sun on this map: the sun was not moved."
        note = ""
        corr = self._corr_ev(path)
        if auto_exposure and (not prev_path or path_key(prev_path) != path_key(path)):
            self._set_exposure(0.0)                      # другая карта — Exposure с нуля, поправка яркости применяется сама
            note = " Exposure 0.00 EV"
            if abs(corr) >= 0.005:
                note += ", brightness balancing %+.2f EV applied" % corr
            note += "."
        self._status("Applied: %s.%s%s" % (os.path.basename(path), note, sun_note))
        return True

    def _set_exposure(self, ev):
        self._loading = True
        self.sp_exp.setValue(float(ev))
        self._loading = False
        self._on_hdri_setting()
        self._update_current_label(self._current_path())

    @undoable("Brightness balancing")
    def apply_norm_to_current(self):
        """Включено выравнивание: поправка карты применяется под капотом, Exposure пользователя — 0."""
        path = self._current_path()
        off = self.lib.norm_offset(path) if path else None
        if off is None:
            self._status("There is no brightness measurement for the current map.")
            return
        self._set_exposure(0.0)
        self._status("Brightness balancing %+.2f EV applied (Exposure 0.00)." % off)

    def refresh_norm_current(self):
        """Эталон/режим изменились — пересчитать поправку текущей карты, Exposure пользователя не трогаем."""
        if self._current_path():
            self._on_hdri_setting()
            self._update_current_label(self._current_path())

    @undoable("Brightness balancing reset")
    def reset_norm_current(self):
        if self._current_path():
            self._set_exposure(0.0)

    def reload_hdr(self):
        def do():
            _cc, bm, _o = get_chain()
            if bm is None:
                return None
            p = str(bm.filename)
            bm.filename = ""
            bm.filename = p
            return p
        p = self._run("reload HDR", do)
        self._status("Reloaded: %s" % os.path.basename(p) if p else "No HDRI to reload.")

    # ---------- настройки HDRI ----------
    def _hdri_values(self):
        return dict(rotation=self.sp_rot.value(), exposure=self.sp_exp.value(),
                    gamma=self.sp_gamma.value(), saturation=self.sp_sat.value(),
                    blur=self.sp_blur.value(), horizon=self.sp_horizon.value(),
                    cam_ev=self.sp_cam.value(), refl_ev=self.sp_refl.value(), refr_ev=self.sp_refr.value())

    def _corr_ev(self, path):
        """Поправка выравнивания яркости для карты (EV); 0, если выравнивание выключено."""
        try:
            off = self.lib.norm_offset(path) if path else None
        except Exception:
            off = None
        return float(off) if off is not None else 0.0

    def _write_hdri(self, rotation, exposure, gamma, saturation, blur=BLUR_DEFAULT, horizon=0.0,
                    cam_ev=0.0, refl_ev=0.0, refr_ev=0.0):
        adopt_legacy()
        _cc, bm, _o = get_chain()
        if bm is None:
            return False
        cc = ensure_cc(*get_chain()[:2])
        bm.uAngle = float(rotation)
        bm.filteringBlur = min(BLUR_MAX, max(BLUR_MIN, float(blur)))
        bm_set_horizon(bm, max(HORIZON_MIN, min(HORIZON_MAX, float(horizon))))
        cc_write(cc, float(exposure) + self._corr_ev(str(bm.filename)), gamma, saturation)   # Exposure в окне + поправка яркости карты; 100 % = норма
        kind = self.lib.channel_target
        clear_channels(keep_kind=kind)            # в другом месте наших карт не остаётся
        skipped = []
        no_cam = False
        for (ch, name), ev in zip(CHANNELS, (cam_ev, refl_ev, refr_ev)):
            res = set_channel_ev(kind, ch, name, float(ev), cc)
            if res is None:
                no_cam = True
            elif not res:
                skipped.append(ch)
        self._skipped_channels = skipped
        self._no_cam = no_cam
        return True

    def _refresh_target_label(self):
        """Подпись пункта камеры: полное имя активной CoronaCam (если вид не из CoronaCam — «Активная CoronaCam»)."""
        try:
            c = active_corona_cam()
            name = str(c.name) if c is not None else ""
        except Exception:
            name = ""
        txt = name or "Active CoronaCam"
        i = self.cmb_target.findData(TARGET_CAMERA)
        if i >= 0 and self.cmb_target.itemText(i) != txt:
            self.cmb_target.setItemText(i, txt)

    def _on_target_changed(self, *_):
        self.lib.channel_target = self.cmb_target.currentData() or TARGET_RENDERER
        try:
            self.lib.save()
        except Exception:
            print("[SkyRig Studio] library save error:\n" + traceback.format_exc())
        self._on_hdri_setting()                 # перенести текущие значения в выбранное место

    def _on_rot_spin(self, *_):
        if self._loading:
            return
        v = self.sp_rot.value()
        self._loading = True
        self.sl_rot.setValue(rot_to_slider(v))
        self._loading = False
        self._on_hdri_setting()

    def _on_rot_slider(self, v):
        if self._loading:
            return
        self._loading = True
        self.sp_rot.setValue(float(v))
        self._loading = False
        self._on_hdri_setting()

    @undoable("HDRI settings")
    def _on_hdri_setting(self, *_):
        if self._loading:
            return
        vals = self._hdri_values()
        alog("  HDRI " + " ".join("%s=%.3g" % (k, v) for k, v in vals.items() if isinstance(v, (int, float))))
        self._skipped_channels = []
        self._no_cam = False
        res = self._run("HDRI settings", lambda: self._write_hdri(**vals))
        if res is False:
            self._status("Apply the HDRI first (click a preview).")
            return
        # привязка: после поворота HDRI солнце остаётся на яркой точке карты;
        # при сдвиге горизонта меняется и высота
        if res and self.chk_link.isChecked() and find_sun() is not None:
            moved = abs(vals["horizon"] - self._last_horizon) > 1e-6
            self._align_impl(create_if_missing=False, quiet=True, azimuth_only=not moved)
        self._last_horizon = vals["horizon"]
        if self._no_cam:
            self._status("The active view is not a CoronaCam: Direct/Reflect/Refract EV were not written for the camera.", error=True)
            return
        if self._skipped_channels:
            self._status("The %s channels already hold someone else's map: left untouched." % ", ".join(self._skipped_channels), error=True)
            return
        self._refresh_state()

    # ---------- линия горизонта ----------
    def _on_hline(self, on):
        if self._loading:
            return
        cam = view_camera()
        if cam is None:
            self._status("Activate a camera view first: the horizon line is shown in the camera of the current view.",
                         error=True)
            self._sync_hline()
            return
        if self._run("horizon line", lambda: set_cam_hline(cam, on)) is not True:
            self._status("Camera “%s” has no horizon line option." % str(cam.name), error=True)
            self._sync_hline()
            return
        hz = self.sp_horizon.value()
        if on:
            note = (" The HDRI horizon is shifted by %+.2f°, so the map horizon is %s this line." %
                    (hz, "above" if hz * HORIZON_ELEV_SIGN > 0 else "below")) if abs(hz) > 0.01 else ""
            self._status("Horizon line shown in camera “%s”.%s" % (str(cam.name), note))
        else:
            self._status("Horizon line hidden in camera “%s”." % str(cam.name))

    def _sync_hline(self):
        """Переключатель показывает состояние линии горизонта у камеры текущего вида."""
        if not hasattr(self, "chk_hline"):
            return
        st = get_cam_hline(view_camera())
        prev = self._loading
        self._loading = True
        self.chk_hline.setChecked(bool(st))
        self._loading = prev

    @undoable("HDRI settings reset")
    def reset_hdri_settings(self):
        vals = dict(rotation=0.0, exposure=0.0, gamma=1.0, saturation=100.0, blur=BLUR_DEFAULT,
                    horizon=0.0, cam_ev=0.0, refl_ev=0.0, refr_ev=0.0)
        if self._run("reset HDRI", lambda: self._write_hdri(**vals)) is False:
            self._status("Apply the HDRI first.")
            return
        self.sync_from_scene()
        if self.chk_link.isChecked() and find_sun() is not None:
            self._align_impl(create_if_missing=False, quiet=True, azimuth_only=True)

    @undoable("Reset all")
    def reset_all(self):
        sun = find_sun()
        sun_txt = "sun “%s” (with target and icon)" % str(sun.name) if sun is not None else "no sun in the scene"
        r = QtWidgets.QMessageBox.question(
            self, "Reset all values",
            "This will:\n• remove %s;\n• clear the Single map slot (Scene Environment, Corona);\n"
            "• return all parameters to defaults.\n\nContinue? (Cancel: Ctrl+Z.)" % sun_txt)
        if r != QtWidgets.QMessageBox.Yes:
            return

        def do():
            s = find_sun()
            ic = find_icon()
            if s is not None:
                tgt = s.target if bool(s.targeted) else None
                if ic is not None:
                    rt.delete(ic)
                    ic = None
                rt.delete(s)
                if tgt is not None:
                    try:
                        rt.delete(tgt)
                    except Exception:
                        pass
            elif ic is not None:
                rt.delete(ic)
            note = []
            r = corona_renderer()
            if r is not None:
                clear_channels()
                cur = r.bg_texmap
                if cur is None or _our_chain(cur) is not None:
                    r.bg_texmap = None
                    r.bg_source = 0           # снова «3ds Max settings (Environment tab)»
                else:
                    note.append("someone else's map in Single map left untouched")
            if _our_chain(rt.environmentMap) is not None:
                rt.environmentMap = None
            return note

        res = self._run("reset all", do)
        if res is None:
            return
        reset_note = ("; " + "; ".join(res)) if res else ""
        self._loading = True
        try:
            self.sp_rot.setValue(0.0)
            self.sl_rot.setValue(0)
            self.sp_exp.setValue(0.0)
            self.sp_gamma.setValue(1.0)
            self.sp_sat.setValue(100.0)
            self.sp_blur.setValue(BLUR_DEFAULT)
            self.sp_horizon.setValue(0.0)
            self.sp_cam.setValue(0.0)
            self.sp_refl.setValue(0.0)
            self.sp_refr.setValue(0.0)
            self.sp_elev.setValue(35.0)
            self.sp_azim.setValue(180.0)
            self.sp_int.setValue(1.0)
            self.sp_size.setValue(1.0)
            self.sp_dist.setValue(SUN_DIST_DEFAULT)
            self.sp_icon.setValue(ICON_SIZE_DEFAULT)
            self.chk_on.setChecked(True)
            self.chk_link.setChecked(False)
        finally:
            self._loading = False
        self._active_preset = None
        self.sync_from_scene()
        self._status("Reset: sun removed, Single map slot cleared, default values.%s" % reset_note)

    # ---------- солнце ----------
    @undoable("Sun creation")
    def create_sun(self):
        if find_sun() is not None:
            self._status("There is already a sun in the scene; not creating a second one.")
            return

        def do():
            s = rt.CoronaSun()
            s.name = SUN_NAME
            s.pos = rt.point3(0, 0, 0)
            set_sun_direction(s, 35.0, 180.0, m_to_units(SUN_DIST_DEFAULT))
            ensure_icon(s, m_to_units(ICON_SIZE_DEFAULT))
            return True

        if self._run("create sun", do):
            self._status("Sun created: the target is at the scene center (0, 0, 0).")
            self.sync_from_scene()
            if self.chk_link.isChecked() and self._current_path():
                self._align_impl(create_if_missing=False, quiet=True)   # сразу на яркую точку HDRI

    def select_sun(self):
        s = find_sun()
        if s is None:
            self._status("Sun not found.")
            return
        self._run("select sun", lambda: rt.select(s))

    def _on_azimuth_manual(self, *_):
        """Ручная правка азимута разрывает привязку к HDRI."""
        if self._loading:
            return
        if self.chk_link.isChecked():
            self.chk_link.setChecked(False)
        self._on_sun_setting()

    @undoable("Compass: sun azimuth")
    def _on_compass_click(self, az):
        """Клик по компасу: солнце — на выбранный азимут (высота прежняя), HDRI поворачивается так,
        чтобы её самый яркий пиксель остался под солнцем."""
        s = find_sun()
        path = self._current_path()
        if s is None:
            self._status("Create the sun first.", error=True)
            return
        if not path or not os.path.isfile(path):
            self._status("Apply an HDRI with an existing file first.", error=True)
            return
        try:
            pt = self._hdri_sun_point(path)
        except Exception:
            print("[SkyRig Studio] compass click error:\n" + traceback.format_exc())
            pt = None
        if pt is None:
            self._status("Could not find the bright point of the HDRI.", error=True)
            return
        u = pt[0]
        z = s.transform.row3
        cur_el, _a = angles_from_z((float(z.x), float(z.y), float(z.z)))
        cur_rot = self.sp_rot.value()
        target = (-360.0 * u - az) % 360.0
        new_rot = cur_rot + (((target - cur_rot) + 180.0) % 360.0 - 180.0)   # ближайший поворот к текущему
        self._loading = True
        self.sp_rot.setValue(new_rot)
        self.sl_rot.setValue(rot_to_slider(new_rot))
        self._loading = False
        self._on_hdri_setting()                     # записать Rotation в карту (при привязке сдвинет и солнце)
        s = find_sun()
        if s is not None:
            self._run("compass sun", lambda: set_sun_direction(s, cur_el, az))
            self._loading = True
            self.sp_azim.setValue(az)
            self._loading = False
        self._refresh_state()
        self._status("Sun: azimuth %.1f° (elevation %.1f° unchanged), HDRI Rotation %.2f°." % (az, cur_el, new_rot))

    @undoable("Sun settings")
    def _on_sun_setting(self, *_):
        if self._loading:
            return
        elev, azim = self.sp_elev.value(), self.sp_azim.value()
        inten, size, on = self.sp_int.value(), self.sp_size.value(), self.chk_on.isChecked()
        dist, icon = m_to_units(self.sp_dist.value()), m_to_units(self.sp_icon.value())
        alog("SUN  elev=%.2f azim=%.2f int=%.3g size=%.3g on=%s" % (elev, azim, inten, size, on))

        def do():
            s = find_sun()
            if s is None:
                return False
            set_sun_direction(s, elev, azim, dist)
            s.intensity = inten
            s.sizeMultiplier = size
            s.on = on
            ensure_icon(s, icon)
            return True

        if self._run("sun settings", do) is False:
            self._status("Sun not found: press “Create at center”.")
        self._refresh_state()

    # ---------- совмещение солнца с HDRI ----------
    def _find_progress(self, pc, text):
        self.prog_find.setValue(int(pc))
        self.prog_find.setFormat("%s  %%p%%" % text if pc < 100 else text)
        QtWidgets.QApplication.processEvents()
        return True

    def _hdri_sun_point(self, path, force=False):
        """(u, v, peak) из кэша или поиском (≈2-3 с на 8K, с прогресс-баром). None при неудаче."""
        c = None if force else self.sun_cache.get(path)
        if c:
            return c["u"], c["v"], c["peak"]
        self._wait_cursor(True)
        self.prog_find.setValue(0)
        self.prog_find.setFormat("Preparing...")
        self.prog_find.setVisible(True)
        QtWidgets.QApplication.processEvents()
        try:
            res = find_brightest(path, self._find_progress)
        finally:
            self.prog_find.setVisible(False)
            self._wait_cursor(False)
        if res is None:
            return None
        self.sun_cache.put(path, *res)
        self._update_current_label(path)               # теперь пик точный
        return res

    def align_sun(self):
        """Кнопка «link to HDRI»: совмещает солнце с яркой точкой и включает привязку азимута к HDRI."""
        if self._align_impl(create_if_missing=True, quiet=False, force_search=True):
            self.chk_link.setChecked(True)

    @undoable("Link sun to HDRI")
    def _align_impl(self, create_if_missing, quiet, azimuth_only=False, force_search=False):
        path = self._current_path()
        if not path or not os.path.isfile(path):
            if not quiet:
                self._status("Apply an HDRI with an existing file first.", error=True)
            return
        try:
            pt = self._hdri_sun_point(path, force=force_search)
        except Exception:
            print("[SkyRig Studio] find brightest error:\n" + traceback.format_exc())
            self._status("Could not find the bright point (see Listener).", error=True)
            return
        if pt is None:
            self._status("Could not read the HDRI.", error=True)
            return
        u, v, peak = pt
        _cc, bm, _o = get_chain()
        rot = float(bm.uAngle) if bm is not None else 0.0
        az, el = hdri_pixel_to_direction(u, v, rot, bm_horizon(bm) if bm is not None else 0.0)
        warn = ""
        if el < 0:
            warn = " The bright point is below the horizon: elevation limited to 0°."
        el_c = max(0.0, min(90.0, el))
        if peak < SUN_MIN_PEAK:
            warn += " The peak brightness is low (%.1f): there seems to be no distinct sun on the map." % peak

        if find_sun() is None:
            if not create_if_missing:
                return
            self.create_sun()
            if find_sun() is None:
                return

        def do():
            s = find_sun()
            if s is None:
                return False
            if azimuth_only:
                z = s.transform.row3
                cur_el, _a = angles_from_z((float(z.x), float(z.y), float(z.z)))
                set_sun_direction(s, cur_el, az)
            else:
                set_sun_direction(s, el_c, az)
            return True

        if self._run("align sun", do):
            self._loading = True
            self.sp_azim.setValue(az)
            if not azimuth_only:
                self.sp_elev.setValue(el_c)
            self._loading = False
            self._refresh_state()                 # красная надпись / крестик — в том числе после тихого выравнивания
            if not quiet:
                self._status("Sun aligned with the HDRI: Azimuth %.2f°, Height %.2f° "
                             "(peak %.0f, point u=%.4f v=%.4f).%s" % (az, el_c, peak, u, v, warn))
            return True

    # ---------- пресеты ----------
    def _refresh_presets(self, select=None):
        self.cmb_presets.clear()
        for n in self.presets.names():
            self.cmb_presets.addItem(n)
        if select:
            i = self.cmb_presets.findText(select)
            if i >= 0:
                self.cmb_presets.setCurrentIndex(i)

    def _collect_preset(self):
        d = dict(self._hdri_values())
        d["hdri"] = self._current_path()
        d["fmt"] = 3                      # 2: Saturation 100 = норма; 3: расстояние и значок в метрах
        s = find_sun()
        d["sun"] = None if s is None else dict(
            on=self.chk_on.isChecked(), height=self.sp_elev.value(), azimuth=self.sp_azim.value(),
            intensity=self.sp_int.value(), size=self.sp_size.value(), distance=self.sp_dist.value(),
            icon=self.sp_icon.value(), link=self.chk_link.isChecked())
        return d

    def _store_preset(self, name):
        self.presets.presets[name] = self._collect_preset()
        try:
            self.presets.save()
        except Exception:
            print("[SkyRig Studio] preset save error:\n" + traceback.format_exc())
            self._status("Could not save presets (see Listener).", error=True)
            return False
        return True

    def save_preset_as(self):
        cam = active_camera_name()
        dlg = QtWidgets.QInputDialog(self)
        dlg.setWindowTitle("Save preset")
        dlg.setInputMode(QtWidgets.QInputDialog.TextInput)
        if cam:
            dlg.setLabelText("Preset name (the active camera is filled in: add a variant):")
            dlg.setTextValue(cam + " ")
        else:
            dlg.setLabelText("Preset name:")
        le = dlg.findChild(QtWidgets.QLineEdit)

        def _cursor_to_end():                          # по умолчанию текст выделен и затрётся первым символом
            if le is not None:
                le.deselect()
                le.setCursorPosition(len(le.text()))
        QtCore.QTimer.singleShot(0, _cursor_to_end)
        QtCore.QTimer.singleShot(60, _cursor_to_end)
        ok = dlg.exec() == QtWidgets.QDialog.Accepted
        name = dlg.textValue().strip() if ok else ""
        if not name:
            return
        if name in self.presets.presets:
            r = QtWidgets.QMessageBox.question(self, "Preset exists",
                                               "Preset “%s” already exists. Overwrite?" % name)
            if r != QtWidgets.QMessageBox.Yes:
                return
        if self._store_preset(name):
            self._active_preset = name
            self._refresh_presets(select=name)
            self._status("Preset “%s” saved." % name)

    def update_preset(self):
        name = self.cmb_presets.currentText()
        if not name:
            self._status("Select a preset.")
            return
        if self._store_preset(name):
            self._active_preset = name
            self._status("Preset “%s” updated." % name)

    def export_presets(self):
        if not self.presets.presets:
            self._status("No presets: nothing to save.", error=True)
            return
        names = None
        cur = self.cmb_presets.currentText()
        if cur and len(self.presets.presets) > 1:
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("Presets to file")
            box.setText("What to save?")
            b_one = box.addButton("Only “%s”" % cur, QtWidgets.QMessageBox.AcceptRole)
            b_all = box.addButton("All (%d)" % len(self.presets.presets), QtWidgets.QMessageBox.AcceptRole)
            box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
            box.exec()
            c = box.clickedButton()
            if c is b_one:
                names = [cur]
            elif c is not b_all:
                return
        default = os.path.join(os.path.expanduser("~"), "SkyRigStudio_presets.json")
        path, _f = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save presets", default, "SkyRig Studio presets (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            n = self.presets.export_to(path, names)
        except Exception:
            print("[SkyRig Studio] presets export error:\n" + traceback.format_exc())
            self._status("Could not save the file (see Listener).", error=True)
            return
        self._status("Presets saved: %d. %s" % (n, path))

    def _ask_overwrite(self, path):
        """Файл уже есть: 'yes' — перезаписать, 'rename' — другое имя, 'no' — отмена."""
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Warning)
        box.setWindowTitle("File already exists")
        box.setText("The file already exists and will be overwritten:\n%s\n\nOverwrite?" % path)
        b_yes = box.addButton("Yes, overwrite", QtWidgets.QMessageBox.YesRole)
        b_ren = box.addButton("Save with another name...", QtWidgets.QMessageBox.ActionRole)
        b_no = box.addButton("No", QtWidgets.QMessageBox.NoRole)
        box.setDefaultButton(b_no)
        box.setEscapeButton(b_no)
        self._ask_box = box                      # для тестов: на него можно посмотреть без exec
        box.exec()
        c = box.clickedButton()
        return "yes" if c is b_yes else ("rename" if c is b_ren else "no")

    def _suggest_free_name(self, folder, base):
        k = 2
        while os.path.exists(os.path.join(folder, "%s_%d.presets.json" % (base, k))):
            k += 1
        return "%s_%d" % (base, k)

    def _ask_new_name(self, folder, base):
        """Предлагает свободное имя (ИмяСцены_2). Возвращает имя файла с расширением или None."""
        text, ok = QtWidgets.QInputDialog.getText(
            self, "New file name", "Presets file name (the .presets.json extension is added automatically):",
            QtWidgets.QLineEdit.Normal, self._suggest_free_name(folder, base))
        if not ok:
            return None
        name = text.strip()
        for ext in (".presets.json", ".json"):
            if name.lower().endswith(ext):
                name = name[: -len(ext)]
        name = "".join("_" if ch in '\\/:*?"<>|' else ch for ch in name).strip(" .")
        return (name + ".presets.json") if name else None

    def save_presets_beside_scene(self):
        """Все пресеты — в файл «<имя сцены>.presets.json» в папке открытой сцены, без диалогов."""
        if not self.presets.presets:
            self._status("No presets: nothing to save.", error=True)
            return
        try:
            folder, fname = str(rt.maxFilePath), str(rt.maxFileName)
        except Exception:
            folder, fname = "", ""
        if not folder or not fname:
            self._status("The scene is not saved yet: save it and the file will get a name and a folder.", error=True)
            return
        base = os.path.splitext(fname)[0]
        path = os.path.join(folder, base + ".presets.json")
        while os.path.exists(path):
            ans = self._ask_overwrite(path)
            if ans == "yes":
                break
            if ans != "rename":
                self._status("Saving cancelled, the file was not touched.")
                return
            new = self._ask_new_name(folder, base)
            if not new:
                self._status("Saving cancelled, the file was not touched.")
                return
            path = os.path.join(folder, new)         # если и такой есть — спросим снова
        try:
            n = self.presets.export_to(path)
        except Exception:
            print("[SkyRig Studio] presets beside scene error:\n" + traceback.format_exc())
            self._status("Could not write the file to the scene folder (no permission or the network is unavailable).", error=True)
            return
        self._status("Presets saved: %d. File: %s" % (n, path))

    def import_presets(self):
        path, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load presets", os.path.expanduser("~"), "SkyRig Studio presets (*.json)")
        if not path:
            return
        try:
            incoming = PresetStore.read_backup(path)
        except ValueError as ex:
            self._status("Not loaded: %s." % ex, error=True)
            return
        if not incoming:
            self._status("The file contains no presets.", error=True)
            return
        mode = "rename"
        clash = [n for n in incoming if n in self.presets.presets]
        if clash:
            box = QtWidgets.QMessageBox(self)
            box.setWindowTitle("Presets from file")
            box.setText("Presets in the file: %d. Same names as existing ones: %d (%s).\n\nWhat should be done with them?"
                        % (len(incoming), len(clash), ", ".join(clash[:5]) + ("…" if len(clash) > 5 else "")))
            b_rep = box.addButton("Replace", QtWidgets.QMessageBox.DestructiveRole)
            b_ren = box.addButton("Add with a number", QtWidgets.QMessageBox.AcceptRole)
            b_skip = box.addButton("Skip", QtWidgets.QMessageBox.AcceptRole)
            box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
            box.exec()
            c = box.clickedButton()
            if c is b_rep:
                mode = "replace"
            elif c is b_ren:
                mode = "rename"
            elif c is b_skip:
                mode = "skip"
            else:
                return
        added, replaced, skipped = self.presets.import_presets(incoming, mode)
        try:
            self.presets.save()
        except Exception:
            print("[SkyRig Studio] preset save error:\n" + traceback.format_exc())
            self._status("Could not save presets (see Listener).", error=True)
            return
        self._refresh_presets(select=self.cmb_presets.currentText() or None)
        parts = ["added %d" % added]
        if replaced:
            parts.append("replaced %d" % replaced)
        if skipped:
            parts.append("skipped %d" % skipped)
        self._status("Presets from file: " + ", ".join(parts) + ".")

    def delete_preset(self):
        name = self.cmb_presets.currentText()
        if not name:
            return
        r = QtWidgets.QMessageBox.question(self, "Delete preset", "Delete preset “%s”?" % name)
        if r == QtWidgets.QMessageBox.Yes:
            self.presets.presets.pop(name, None)
            try:
                self.presets.save()
            except Exception:
                pass
            if self._active_preset == name:
                self._active_preset = None
            self._refresh_presets()
            self._status("Preset “%s” deleted." % name)

    @undoable("Loading preset")
    def load_preset(self):
        name = self.cmb_presets.currentText()
        p = self.presets.presets.get(name) if name else None
        if not p:
            self._status("Select a preset.")
            return
        notes = []
        path = p.get("hdri", "")
        if path:
            if os.path.isfile(path):
                if not self.apply_path(path, auto_exposure=False):
                    return
            else:
                notes.append("HDRI file not found: %s" % path)
        sat = float(p.get("saturation", 100.0 if p.get("fmt", 1) >= 2 else 0.0))
        if p.get("fmt", 1) < 2:
            sat += 100.0                  # пресет старого формата: 0 = норма
        vals = dict(rotation=p.get("rotation", 0.0), exposure=p.get("exposure", 0.0),
                    gamma=p.get("gamma", 1.0), saturation=sat, blur=p.get("blur", BLUR_DEFAULT),
                    horizon=p.get("horizon", 0.0), cam_ev=p.get("cam_ev", 0.0),
                    refl_ev=p.get("refl_ev", 0.0), refr_ev=p.get("refr_ev", 0.0))
        if self._run("preset HDRI", lambda: self._write_hdri(**vals)) is False:
            notes.append("HDRI settings not applied (no HDRI)")
        sun = p.get("sun")
        if sun:
            if find_sun() is None:
                self.create_sun()

            def do():
                s = find_sun()
                if s is None:
                    return False
                dist_m = float(sun.get("distance", SUN_DIST_DEFAULT))
                icon_m = float(sun.get("icon", ICON_SIZE_DEFAULT))
                if p.get("fmt", 1) < 3:            # старый формат: единицы сцены -> метры
                    dist_m, icon_m = units_to_m(dist_m), units_to_m(icon_m)
                dist_m = max(SUN_DIST_MIN, min(SUN_DIST_MAX, dist_m))
                set_sun_direction(s, float(sun.get("height", 35.0)), float(sun.get("azimuth", 180.0)),
                                  m_to_units(dist_m))
                ensure_icon(s, m_to_units(icon_m))
                s.intensity = float(sun.get("intensity", 1.0))
                s.sizeMultiplier = float(sun.get("size", 1.0))
                s.on = bool(sun.get("on", True))
                return True

            self._run("preset sun", do)
            self.chk_link.setChecked(bool(sun.get("link", False)))
        self.sync_from_scene()
        if self.chk_link.isChecked() and find_sun() is not None:
            self._align_impl(create_if_missing=False, quiet=True, azimuth_only=True)
        self._active_preset = name
        self._status("Preset “%s” loaded.%s" % (name, (" " + "; ".join(notes)) if notes else ""))

    # ---------- название карты и яркостный диапазон ----------
    def _update_current_label(self, path):
        """Имя файла + динамический диапазон в EV: от тёмного края (0.5 % самых тёмных ненулевых
        пикселей) до самого яркого пикселя. «≈» — пик взят из уменьшенного превью (занижен);
        точное значение появляется после поиска яркой точки (кнопка «link to HDRI» / привязка)."""
        name = os.path.basename(path) or "—"
        rl = res_label(path) if path else ""
        if rl:
            name += " [%s]" % rl
        text, tip = name, path
        if rl:
            sz = image_size(path)
            tip += "\n\nTexture resolution: %d x %d px" % sz
        try:
            if path and os.path.isfile(lin_path(path)):
                key = (path, os.path.getmtime(lin_path(path)))
                if self._range_key != key:
                    self._range_key, self._range_stats = key, lin_stats(path)
                st = self._range_stats
                if st:
                    cached = self.sun_cache.get(path)
                    approx = not (cached and cached.get("peak"))
                    peak = st[0] if approx else float(cached["peak"])
                    ev = range_ev(max(peak, st[0]), st[1])
                    if ev is not None:
                        text = "%s\nRange: %s%.1f EV" % (name, "≈" if approx else "", ev)
                        corr = self._corr_text(path)
                        if corr:
                            text += " " + corr
                        tip = tip + "\n\nDynamic range: from the dark edge (the darkest 0.5 % of pixels) " \
                                     "to the brightest pixel.\n" + (
                            "“≈”: the peak was taken from the preview and is underestimated; more accurate after the bright-point search."
                            if approx else "The peak was measured at full resolution.")
        except Exception:
            pass
        corr = self._corr_text(path) if path else ""
        if corr and "[" not in text:                    # диапазона нет (нет превью) — поправка всё равно видна
            text += "\n" + corr
        if corr:
            tip += "\n\nBrightness balancing correction (from the library): the exposure at which the map gives as much light as the reference."
        self.lbl_current.setText(text)
        self.lbl_current.setToolTip(tip)

    def _corr_text(self, path):
        """«[Correction -4.0 EV]», если выравнивание яркости включено и для карты есть поправка."""
        try:
            off = self.lib.norm_offset(path)
        except Exception:
            off = None
        if off is None:
            return ""
        return "[Correction %s EV]" % ("0.0" if abs(off) < 0.05 else "%+.1f" % off)

    # ---------- чтение состояния сцены в поля ----------
    def sync_from_scene(self):
        self._loading = True
        path = ""
        try:
            self._sync_hline()
            cc, bm, other = get_chain()
            if bm is not None:
                path = str(bm.filename)
                self.lbl_current.setText(os.path.basename(path) or "—")   # диапазон EV — ниже, после превью
                rot = float(bm.uAngle)
                self.sp_rot.setValue(rot)
                try:
                    self.sp_blur.setValue(float(bm.filteringBlur))
                except Exception:
                    pass
                hz = bm_horizon(bm)
                self.sp_horizon.setValue(max(HORIZON_MIN, min(HORIZON_MAX, hz)))
                self._last_horizon = self.sp_horizon.value()
                for sp, (ch, name) in zip((self.sp_cam, self.sp_refl, self.sp_refr), CHANNELS):
                    try:
                        sp.setValue(channel_ev(self.lib.channel_target, ch, name))
                    except Exception:
                        pass
                self.sl_rot.setValue(rot_to_slider(rot))
                if cc is not None:
                    ev_, g_, sat_ = cc_read(cc)
                    self.sp_exp.setValue(round(ev_ - self._corr_ev(path), 2))   # поправка яркости живёт под капотом
                    self.sp_gamma.setValue(g_)
                    self.sp_sat.setValue(min(200.0, sat_))
            elif other:
                self.lbl_current.setText("A different map is in the Single map slot")
            else:
                self.lbl_current.setText("—")

            s = find_sun()
            if s is None:
                self.lbl_sun.setText("Sun: not found")
            else:
                self.lbl_sun.setText("Sun: %s" % str(s.name))
                z = s.transform.row3
                elev, azim = angles_from_z((float(z.x), float(z.y), float(z.z)))
                self.sp_elev.setValue(elev)
                self.sp_azim.setValue(azim)
                self.sp_int.setValue(float(s.intensity))
                self.sp_size.setValue(float(s.sizeMultiplier))
                self.sp_dist.setValue(units_to_m(_sun_distance(s)))
                ic = find_icon()
                self.sp_icon.setValue(units_to_m(float(ic.size)) if ic is not None else ICON_SIZE_DEFAULT)
                self.chk_on.setChecked(bool(s.on))
        except Exception:
            print("[SkyRig Studio] sync error:\n" + traceback.format_exc())
        finally:
            self._loading = False
        key = (path, self.lib.thumb_ev)
        if key != self._shown_key:
            self._update_preview(path)
            self._shown_key = key
        if path:
            self._update_current_label(path)
        self._refresh_state()

    # ---------- обновление значений при входе в панель ----------
    def _user_is_editing(self):
        """True, если пользователь прямо сейчас вводит число или тянет спиннер."""
        for sp in self.findChildren(MaxSpinBox):
            if sp._arrow_press:
                return True
            le = sp.lineEdit()
            if le is not None and le.isModified():       # введено, но ещё не зафиксировано Enter-ом
                return True
        return False

    def refresh_from_scene(self):
        """Один раз читает текущие значения из сцены (после правок в Corona Sun / Environment).
        Без таймеров и callback-ов: вызывается только событиями Qt (вход мыши, активация окна)."""
        if self._busy or self._loading or self._user_is_editing():
            return
        now = time.monotonic()
        if now - self._last_enter_sync < 0.25:        # защита от серии событий подряд
            return
        self._last_enter_sync = now
        self.sync_from_scene()

    def enterEvent(self, e):
        super().enterEvent(e)
        self.refresh_from_scene()

    def event(self, e):
        if e.type() == QtCore.QEvent.WindowActivate:   # переключились на окно/док
            self.refresh_from_scene()
        return super().event(e)

    def showEvent(self, e):
        super().showEvent(e)
        self.sync_from_scene()


# ==========================================================================
# 8. Запуск
# ==========================================================================
def _cleanup_old(main_win):
    for d in main_win.findChildren(QtWidgets.QDockWidget, DOCK_OBJ_NAME):
        try:
            main_win.removeDockWidget(d)
        except Exception:
            pass
        d.close()
        d.deleteLater()
    for name in (WINDOW_OBJ_NAME, LIBRARY_OBJ_NAME):
        for w in main_win.findChildren(QtWidgets.QWidget, name):
            w.close()
            w.deleteLater()


def _alog_session():
    try:
        alog("=== SESSION v%s | scene: %s | renderer: %s" % (
            VERSION, str(rt.maxFileName) or "(untitled)", _class_name(rt.renderers.current)))
    except Exception:
        alog("=== SESSION v%s" % VERSION)


def show():
    """Окно встраивается (dock) в главное окно 3ds Max: прилипает к левому или правому краю.
    Можно перетащить за заголовок к другому краю или оторвать в плавающее окно."""
    main_win = qtmax.GetQMaxMainWindow()
    _cleanup_old(main_win)
    _alog_session()
    win = SafeEnvironment()
    if _UPDATED_FROM:
        win._status("Updated: v%s → v%s." % (_UPDATED_FROM, VERSION))
    else:
        try:
            win.auto_check_updates()
        except Exception:
            print("[SkyRig Studio] update check error:\n" + traceback.format_exc())
    try:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(win)
        dock = QtWidgets.QDockWidget(WINDOW_TITLE, main_win)
        dock.setObjectName(DOCK_OBJ_NAME)
        dock.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetMovable
                         | QtWidgets.QDockWidget.DockWidgetFloatable
                         | QtWidgets.QDockWidget.DockWidgetClosable)
        dock.setWidget(scroll)
        dock.setMinimumWidth(DOCK_WIDTH)
        main_win.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)
        try:
            main_win.resizeDocks([dock], [DOCK_WIDTH], QtCore.Qt.Horizontal)
        except Exception:
            pass   # слева — на всю высоту; справа окно встаёт под Command Panel
        dock.show()
    except Exception:
        print("[SkyRig Studio] dock failed, floating window:\n" + traceback.format_exc())
        win.setParent(main_win)
        win.setWindowFlags(QtCore.Qt.Tool)
        win.show()
    return win


_window = show()
