from asyncio import (create_subprocess_exec, create_subprocess_shell,
                     run_coroutine_threadsafe, sleep)
from asyncio.subprocess import PIPE
from concurrent.futures import ThreadPoolExecutor
from functools import partial, wraps
from html import escape
from re import match
from time import time
from uuid import uuid4
from psutil import disk_usage
from pyrogram.types import BotCommand
from aiohttp import ClientSession

from bot import (bot_loop, bot_name, botStartTime, config_dict, download_dict,
                 download_dict_lock, extra_buttons, user_data)
from bot.helper.ext_utils.shortener import short_url
from bot.helper.ext_utils.telegraph_helper import telegraph
from bot.helper.telegram_helper.bot_commands import BotCommands
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.themes import BotTheme

THREADPOOL = ThreadPoolExecutor(max_workers=10)

MAGNET_REGEX = r'^magnet:\?.*xt=urn:(btih|btmh):[a-zA-Z0-9]*\s*'

URL_REGEX = r'^(?!\/)(rtmps?:\/\/|mms:\/\/|rtsp:\/\/|https?:\/\/|ftp:\/\/)?([^\/:]+:[^\/@]+@)?(www\.)?(?=[^\/:\s]+\.[^\/:\s]+)([^\/:\s]+\.[^\/:\s]+)(:\d+)?(\/[^#\s]*[\s\S]*)?(\?[^#\s]*)?(#.*)?$'

SIZE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']

STATUS_START = 0
PAGES = 1
PAGE_NO = 1

class MirrorStatus:
    STATUS_UPLOADING = "Upload"
    STATUS_DOWNLOADING = "Download"
    STATUS_CLONING = "Clone"
    STATUS_QUEUEDL = "QueueDL"
    STATUS_QUEUEUP = "QueueUP"
    STATUS_PAUSED = "Pause"
    STATUS_ARCHIVING = "Archive"
    STATUS_EXTRACTING = "Extract"
    STATUS_SPLITTING = "Split"
    STATUS_CHECKING = "CheckUP"
    STATUS_SEEDING = "Seed"

class setInterval:
    def __init__(self, interval, action):
        self.interval = interval
        self.action = action
        self.task = bot_loop.create_task(self.__set_interval())

    async def __set_interval(self):
        while True:
            await sleep(self.interval)
            await self.action()

    def cancel(self):
        self.task.cancel()


def get_readable_file_size(size_in_bytes):
    if size_in_bytes is None:
        return '0B'
    index = 0
    while size_in_bytes >= 1024 and index < len(SIZE_UNITS) - 1:
        size_in_bytes /= 1024
        index += 1
    return f'{size_in_bytes:.2f}{SIZE_UNITS[index]}' if index > 0 else f'{size_in_bytes}B'


async def getDownloadByGid(gid):
    async with download_dict_lock:
        return next((dl for dl in download_dict.values() if dl.gid() == gid), None)


async def getAllDownload(req_status, user_id=None):
    dls = []
    async with download_dict_lock:
        for dl in list(download_dict.values()):
            if user_id and user_id != dl.message.from_user.id:
                continue
            status = dl.status()
            if req_status in ['all', status]:
                dls.append(dl)
    return dls


def bt_selection_buttons(id_, isCanCncl=True):
    gid = id_[:12] if len(id_) > 20 else id_
    pincode = ''.join([n for n in id_ if n.isdigit()][:4])
    buttons = ButtonMaker()
    BASE_URL = config_dict['BASE_URL']
    if config_dict['WEB_PINCODE']:
        buttons.ubutton("Select Files", f"{BASE_URL}/app/files/{id_}")
        buttons.ibutton("Pincode", f"btsel pin {gid} {pincode}")
    else:
        buttons.ubutton(
            "Select Files", f"{BASE_URL}/app/files/{id_}?pin_code={pincode}")
    if isCanCncl:
        buttons.ibutton("Cancel", f"btsel rm {gid} {id_}")
    buttons.ibutton("Done Selecting", f"btsel done {gid} {id_}")
    return buttons.build_menu(2)


async def get_telegraph_list(telegraph_content):
    path = [(await telegraph.create_page(title='Z Drive Search', content=content))["path"] for content in telegraph_content]
    if len(path) > 1:
        await telegraph.edit_telegraph(path, telegraph_content)
    buttons = ButtonMaker()
    buttons.ubutton("🔎 VIEW", f"https://graph.org/{path[0]}", 'header')
    buttons = extra_btns(buttons)
    return buttons.build_menu(1)


def get_progress_bar_string(pct):
    pct = float(str(pct).strip('%'))
    p = min(max(pct, 0), 100)
    cFull = int(p // 8)
    cPart = int(p % 8 - 1)
    p_str = '●' * cFull
    if cPart >= 0:
        p_str += ['◔', '◓', '◒', '◑', '◐', '◕', '●'][cPart]
    p_str += '○' * (12 - cFull)
    return f"[{p_str}]"


class EngineStatus:
    STATUS_ARIA = f"Aria2 v{aria2.client.get_version()['version']}"
    STATUS_GD = f"Google-API v{get_distribution('google-api-python-client').version}"
    STATUS_MEGA = f"MegaSDK v{MegaApi('test').getVersion()}"
    STATUS_QB = f"qBit {get_client().app.version}"
    STATUS_TG = f"Pyrogram v{get_distribution('pyrogram').version}"
    STATUS_YT = f"yt-dlp v{get_distribution('yt-dlp').version}"
    STATUS_EXT = "pExtract v2"
    STATUS_SPLIT_MERGE = f"ffmpeg v{get_ffmpeg_version()}"
    STATUS_ZIP = f"p7zip v{get_p7zip_version()}"
    STATUS_QUEUE = "Sleep v0"
    STATUS_RCLONE = f"RClone {get_rclone_version()}"


def get_readable_message():
    msg = ""
    button = None
    STATUS_LIMIT = config_dict['STATUS_LIMIT']
    tasks = len(download_dict)

    globals()['PAGES'] = (tasks + STATUS_LIMIT - 1) // STATUS_LIMIT
    if PAGE_NO > PAGES and PAGES != 0:
        globals()['STATUS_START'] = STATUS_LIMIT * (PAGES - 1)
        globals()['PAGE_NO'] = PAGES
      
    for download in list(download_dict.values())[STATUS_START:STATUS_LIMIT+STATUS_START]:

        tag = download.message.from_user.mention
        if reply_to := download.message.reply_to_message:
            tag = reply_to.from_user.mention

        elapsed = time() - download.extra_details['startTime']

        msg += f"\n<b>File Name</b> » <i>{escape(f'{download.name()}')}</i>\n\n" if elapsed <= config_dict['AUTO_DELETE_MESSAGE_DURATION'] else ""
        msg += f"• <b>{download.status()}</b>"

        if download.status() not in [MirrorStatus.STATUS_SPLITTING, MirrorStatus.STATUS_SEEDING]:
            if download.status() != MirrorStatus.STATUS_UPLOADDDL:
                msg += BotTheme('BAR', Bar=f"{get_progress_bar_string(download.progress())} {download.progress()}")
                msg += BotTheme('PROCESSED', Processed=f"{download.processed_bytes()} of {download.size()}")
            msg += BotTheme('STATUS', Status=download.status(), Url=msg_link)
            if download.status() != MirrorStatus.STATUS_UPLOADDDL:
                msg += BotTheme('ETA', Eta=download.eta())
                msg += BotTheme('SPEED', Speed=download.speed())
            msg += BotTheme('ELAPSED', Elapsed=get_readable_time(time() - download.message.date.timestamp()))
            msg += BotTheme('ENGINE', Engine=download.eng())
            msg += BotTheme('STA_MODE', Mode=download.upload_details['mode'])
            if hasattr(download, 'playList'):
                try:
                    if playlist:=download.playList():
                        msg += f"\n• <code>YT Count </code>» {playlist}"
                except:
                    pass

            if hasattr(download, 'seeders_num'):
                try:
                    msg += BotTheme('SEEDERS', Seeders=download.seeders_num())
                    msg += BotTheme('LEECHERS', Leechers=download.leechers_num())
                except:
                    pass
        elif download.status() == MirrorStatus.STATUS_SEEDING:
            msg += BotTheme('STATUS', Status=download.status(), Url=msg_link)
            msg += BotTheme('SEED_SIZE', Size=download.size())
            msg += BotTheme('SEED_SPEED', Speed=download.upload_speed())
            msg += BotTheme('UPLOADED', Upload=download.uploaded_bytes())
            msg += BotTheme('RATIO', Ratio=download.ratio())
            msg += BotTheme('TIME', Time=download.seeding_time())
            msg += BotTheme('SEED_ENGINE', Engine=download.eng())

        else:
            msg += BotTheme('STATUS', Status=download.status(), Url=msg_link)
            msg += BotTheme('STATUS_SIZE', Size=download.size())
            msg += BotTheme('NON_ENGINE', Engine=download.eng())
        msg += BotTheme('USER',
                      User=download.message.from_user.mention(style="html"))
        msg += BotTheme('ID', Id=download.message.from_user.id)
        if (download.eng()).startswith("qBit"):
            msg += BotTheme('BTSEL', Btsel=f"/{BotCommands.BtSelectCommand}_{download.gid()}")
        msg += BotTheme('CANCEL', Cancel=f"/{BotCommands.CancelMirror}_{download.gid()}")
        if config_dict['DELETE_LINKS']:
            msg += f"\n• <code>Task     </code>» {download.extra_details['mode']}"
        else:
            msg += f"\n• <code>Task     </code>» <a href='{download.message.link}'>{download.extra_details['mode']}</a>"

    if len(msg) == 0:
        return None, None

    def convert_speed_to_bytes_per_second(spd):
        if 'K' in spd:
            return float(spd.split('K')[0]) * 1024
        elif 'M' in spd:
            return float(spd.split('M')[0]) * 1048576
        else:
            return 0

    dl_speed = 0
    up_speed = 0
    for download in download_dict.values():
        tstatus = download.status()
        spd = download.speed() if tstatus != MirrorStatus.STATUS_SEEDING else download.upload_speed()
        speed_in_bytes_per_second = convert_speed_to_bytes_per_second(spd)
        if tstatus == MirrorStatus.STATUS_DOWNLOADING:
            dl_speed += speed_in_bytes_per_second
        elif tstatus == MirrorStatus.STATUS_UPLOADING or tstatus == MirrorStatus.STATUS_SEEDING:
            up_speed += speed_in_bytes_per_second

    msg += BotTheme('FOOTER')
    buttons = ButtonMaker()
    buttons.ibutton(BotTheme('REFRESH', Page=f"{PAGE_NO}/{PAGES}"), "status ref")
    if tasks > STATUS_LIMIT:
        if config_dict['BOT_MAX_TASKS']:
            msg += BotTheme('BOT_TASKS', Tasks=tasks, Ttask=config_dict['BOT_MAX_TASKS'], Free=config_dict['BOT_MAX_TASKS']-tasks)
        else:
            msg += BotTheme('TASKS', Tasks=tasks)
        buttons = ButtonMaker()
        buttons.ibutton(BotTheme('PREVIOUS'), "status pre")
        buttons.ibutton(BotTheme('REFRESH', Page=f"{PAGE_NO}/{PAGES}"), "status ref")
        buttons.ibutton(BotTheme('NEXT'), "status nex")
    button = buttons.build_menu(3)

    msg += BotTheme('Cpu', cpu=cpu_percent())
    msg += BotTheme('DISK', free=get_readable_file_size(disk_usage(config_dict['DOWNLOAD_DIR']).free), free_p=round(100-disk_usage(config_dict['DOWNLOAD_DIR']).percent, 1))
    msg += BotTheme('uptime', uptime=get_readable_time(time() - botStartTime))
    msg += BotTheme('DL', DL=get_readable_file_size(dl_speed))
    msg += BotTheme('UL', UL=get_readable_file_size(up_speed))"
    remaining_time = 86400 - (time() - botStartTime)
    res_time = '⚠️ ANYTIME ⚠️' if remaining_time <= 0 else get_readable_time(remaining_time)
    if remaining_time <= 3600:
        msg += f"\n<b>Bot Restarts In:</b> <code>{res_time}</code>"
    return msg, button


async def turn_page(data):
    STATUS_LIMIT = config_dict['STATUS_LIMIT']
    global STATUS_START, PAGE_NO, PAGES
    async with download_dict_lock:
        if data[1] == "nex" and PAGE_NO == PAGES:
            PAGE_NO = 1
        elif data[1] == "nex" and PAGE_NO < PAGES:
            PAGE_NO += 1
        elif data[1] == "pre" and PAGE_NO == 1:
            PAGE_NO = PAGES
        elif data[1] == "pre" and PAGE_NO > 1:
            PAGE_NO -= 1
        STATUS_START = (PAGE_NO - 1) * STATUS_LIMIT


def get_readable_time(seconds):
    periods = [('d', 86400), ('h', 3600), ('m', 60), ('s', 1)]
    result = ''
    for period_name, period_seconds in periods:
        if seconds >= period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            result += f'{int(period_value)}{period_name}'
    return result


def is_magnet(url):
    return bool(match(MAGNET_REGEX, url))


def is_url(url):
    return bool(match(URL_REGEX, url))


def is_gdrive_link(url):
    return "drive.google.com" in url


def is_telegram_link(url):
    return url.startswith(('https://t.me/', 'tg://openmessage?user_id='))


def is_share_link(url: str):
    if 'gdtot' in url:
        regex = r'(https?:\/\/.+\.gdtot\..+\/file\/\d+)'
    else:
        regex = r'(https?:\/\/(\S+)\..+\/file\/\S+)'
    return bool(match(regex, url))


def is_mega_link(url):
    return "mega.nz" in url or "mega.co.nz" in url


def is_rclone_path(path):
    return bool(match(r'^(mrcc:)?(?!magnet:)(?![- ])[a-zA-Z0-9_\. -]+(?<! ):(?!.*\/\/).*$|^rcl$', path))


def get_mega_link_type(url):
    return "folder" if "folder" in url or "/#F!" in url else "file"

def arg_parser(items, arg_base):
    if not items:
        return arg_base
    bool_arg_set = {
                    '-b', '-bulk', 
                    '-e', '-uz', '-unzip', 
                    '-z', '-zip', 
                    '-s', '-select', 
                    '-j', '-join', 
                    '-d', '-seed'}
    t = len(items)
    m = 0
    arg_start = -1
    while m + 1 <= t:
        part = items[m].strip()
        if part in arg_base:
            if arg_start == -1:
                arg_start = m
            if m + 1 == t and part in bool_arg_set or part in [
                                                                '-s', '-select', 
                                                                '-j', '-join'
                                                            ]:
                arg_base[part] = True
            else:
                sub_list = []
                for j in range(m + 1, t):
                    item = items[j].strip()
                    if item in arg_base:
                        if part in bool_arg_set and not sub_list:
                            arg_base[part] = True
                        break
                    sub_list.append(item.strip())
                    m += 1
                if sub_list:
                    arg_base[part] = " ".join(sub_list)
        m += 1
    link = []
    if items[0].strip() not in arg_base:
        if arg_start == -1:
            link.extend(item.strip() for item in items)
        else:
            link.extend(items[r].strip() for r in range(arg_start))
        if link:
            arg_base['link'] = " ".join(link)
    return arg_base


async def get_content_type(url):
    try:
        async with ClientSession(trust_env=True) as session:
            async with session.get(url, verify_ssl=False) as response:
                return response.headers.get('Content-Type')
    except:
        return None


def update_user_ldata(id_, key=None, value=None):
    exception_keys = ['is_sudo', 'is_auth', 'dly_tasks']
    if not key and not value:
        if id_ in user_data:
            updated_data = {}
            for k, v in user_data[id_].items():
                if k in exception_keys:
                    updated_data[k] = v
            user_data[id_] = updated_data
        return
    user_data.setdefault(id_, {})
    user_data[id_][key] = value


def extra_btns(buttons):
    if extra_buttons:
        for btn_name, btn_url in extra_buttons.items():
            buttons.ubutton(btn_name, btn_url)
    return buttons


async def check_user_tasks(user_id, maxtask):
    downloading_tasks   = await getAllDownload(MirrorStatus.STATUS_DOWNLOADING, user_id)
    uploading_tasks     = await getAllDownload(MirrorStatus.STATUS_UPLOADING, user_id)
    queuedl_tasks       = await getAllDownload(MirrorStatus.STATUS_QUEUEDL, user_id)
    queueup_tasks       = await getAllDownload(MirrorStatus.STATUS_QUEUEUP, user_id)
    total_tasks         = downloading_tasks + uploading_tasks + queuedl_tasks + queueup_tasks
    return len(total_tasks) >= maxtask


def checking_access(user_id, button=None):
    if not config_dict['TOKEN_TIMEOUT'] or bool(user_id == OWNER_ID or user_id in user_data and user_data[user_id].get('is_sudo')):
        return None, button
    user_data.setdefault(user_id, {})
    data = user_data[user_id]
    expire = data.get('time')
    if config_dict['LOGIN_PASS'] is not None and data.get('token', '') == config_dict['LOGIN_PASS']:
        return None, button
    isExpired = (expire is None or expire is not None and (time() - expire) > config_dict['TOKEN_TIMEOUT'])
    if isExpired:
        token = data['token'] if expire is None and 'token' in data else str(uuid4())
        if expire is not None:
            del data['time']
        data['token'] = token
        user_data[user_id].update(data)
        if button is None:
            button = ButtonMaker()
        encrypt_url = b64encode(f"{token}&&{user_id}".encode()).decode()
        button.ubutton('Generate New Token', short_url(f'https://t.me/{bot_name}?start={encrypt_url}'))
        return 'Temp Token is expired, generate a new temp token and try again.', button
    return None, button


async def cmd_exec(cmd, shell=False):
    if shell:
        proc = await create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)
    else:
        proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
    stdout, stderr = await proc.communicate()
    stdout = stdout.decode().strip()
    stderr = stderr.decode().strip()
    return stdout, stderr, proc.returncode


def new_task(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return bot_loop.create_task(func(*args, **kwargs))
    return wrapper


async def sync_to_async(func, *args, wait=True, **kwargs):
    pfunc = partial(func, *args, **kwargs)
    future = bot_loop.run_in_executor(THREADPOOL, pfunc)
    return await future if wait else future


def async_to_sync(func, *args, wait=True, **kwargs):
    future = run_coroutine_threadsafe(func(*args, **kwargs), bot_loop)
    return future.result() if wait else future


def new_thread(func):
    @wraps(func)
    def wrapper(*args, wait=False, **kwargs):
        future = run_coroutine_threadsafe(func(*args, **kwargs), bot_loop)
        return future.result() if wait else future
    return wrapper


async def set_commands(client):
    if config_dict['SET_COMMANDS']:
        await client.set_bot_commands([
            BotCommand(f'{BotCommands.MirrorCommand[0]}', f'or /{BotCommands.MirrorCommand[1]} Mirror'),
            BotCommand(f'{BotCommands.LeechCommand[0]}', f'or /{BotCommands.LeechCommand[1]} Leech'),
            BotCommand(f'{BotCommands.QbMirrorCommand[0]}', f'or /{BotCommands.QbMirrorCommand[1]} Mirror torrent using qBittorrent'),
            BotCommand(f'{BotCommands.QbLeechCommand[0]}', f'or /{BotCommands.QbLeechCommand[1]} Leech torrent using qBittorrent'),
            BotCommand(f'{BotCommands.YtdlCommand[0]}', f'or /{BotCommands.YtdlCommand[1]} Mirror yt-dlp supported link'),
            BotCommand(f'{BotCommands.YtdlLeechCommand[0]}', f'or /{BotCommands.YtdlLeechCommand[1]} Leech through yt-dlp supported link'),
            BotCommand(f'{BotCommands.CloneCommand}', 'Copy file/folder to Drive'),
            BotCommand(f'{BotCommands.CountCommand}', '[drive_url]: Count file/folder of Google Drive.'),
            BotCommand(f'{BotCommands.StatusCommand[0]}', f'or /{BotCommands.StatusCommand[1]} Get mirror status message'),
            BotCommand(f'{BotCommands.StatsCommand[0]}', f'{BotCommands.StatsCommand[1]} Check bot stats'),
            BotCommand(f'{BotCommands.BtSelectCommand}', 'Select files to download only torrents'),
            BotCommand(f'{BotCommands.CategorySelect}', 'Select category to upload only mirror'),
            BotCommand(f'{BotCommands.CancelMirror}', 'Cancel a Task'),
            BotCommand(f'{BotCommands.CancelAllCommand[0]}', f'Cancel all tasks which added by you or {BotCommands.CancelAllCommand[1]} to in bots.'),
            BotCommand(f'{BotCommands.ListCommand}', 'Search in Drive'),
            BotCommand(f'{BotCommands.SearchCommand}', 'Search in Torrent'),
            BotCommand(f'{BotCommands.UserSetCommand}', 'Users settings'),
            BotCommand(f'{BotCommands.HelpCommand}', 'Get detailed help'),
        ])
            await client.set_bot_commands(bot_cmds)
            LOGGER.info('Bot Commands have been Set & Updated')
        except Exception as err:
            LOGGER.error(err)



def is_valid_token(url, token):
    resp = rget(url=f"{url}getAccountDetails?token={token}&allDetails=true").json()
    if resp["status"] == "error-wrongToken":
        raise Exception("Invalid Gofile Token, Get your Gofile token from --> https://gofile.io/myProfile")
