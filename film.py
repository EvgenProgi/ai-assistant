import os
import json
import requests
import vlc
import pyaudio
from vosk import Model, KaldiRecognizer
import sys
import codecs
import requests


sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())

# ================= НАСТРОЙКИ ПАПОК И СЕТИ =================
MOVIES_DIR = "D:/фильмы"
MUSIC_DIR = "C:/Music"

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "mistral"

# Инициализация VLC (Добавлен флаг --video-on-top, чтобы видео открывалось поверх окон)
Instance = vlc.Instance('--no-xlib --quiet --video-on-top')
Player = Instance.media_player_new()
current_playlist = []
current_index = 0
current_volume = 70 

# Автоисправление раскладки
ENG_CHARS = "qwertyuiop[]asdfghjkl;'zxcvbnm,.QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>?"
RUS_CHARS = "йцукенгшщзхъфывапролджэячсмитьбюЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,"
LAYOUT_MAP = str.maketrans(ENG_CHARS, RUS_CHARS)

# Словарь чисел для перевода из текста в цифры
RU_NUMBERS = {
    "ноль": 0, "один": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
    "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20, "тридцать": 30,
    "сорок": 40, "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
    "сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500, "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900
}

def russian_to_translit(text):
    """Переводит русский текст в транслит для поиска файлов типа Doshkolnoe.obrazovanie"""
    legend = {'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'yo','ж':'zh','з':'z','и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'h','ц':'ts','ч':'ch','ш':'sh','щ':'sch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya'}
    return ''.join([legend.get(char, char) for char in text.lower()])

def fix_layout(text):
    words = text.split()
    fixed_words = []
    for word in words:
        w_low = word.lower()
        if w_low in ["drk.xb", "drk.x_", "drk.xt"]: word = "включи"
        elif w_low == "cktle.obq": word = "следующий"
        elif w_low == "gfeph" or w_low == "gfepf": word = "пауза"
        elif w_low == "ypfl": word = "назад"
        elif w_low == "dgthtl": word = "вперед"
        elif w_low == "ndit" or w_low == "nbit": word = "тише"
        elif w_low == "uhjvxt": word = "громче"
        elif w_low == "cnjg": word = "стоп"
        elif w_low == "uhjvrjcnm": word = "громкость"
        elif w_low == "yjkm": word = "ноль"
        elif w_low == "vbyen" or w_low == "vby": word = "минут"
        elif w_low == "yf": word = "на"
        elif w_low == "dsrk.xb": word = "выключи"
        elif w_low == "pder": word = "звук"        
        fixed_words.append(word)
    return " ".join(fixed_words)

def words_to_number(text):
    clean_text = text.replace(" на ", " ").replace(" в ", " ").strip()
    words = clean_text.split()
    total = 0
    has_number = False
    for word in words:
        w_trunc = word.lower()
        if w_trunc.endswith(("ый", "ой", "ое", "ая", "ому", "го")):
            for num_word in RU_NUMBERS:
                if num_word in w_trunc and len(num_word) > 3:
                    w_trunc = num_word
                    break
        if w_trunc in RU_NUMBERS:
            total += RU_NUMBERS[w_trunc]
            has_number = True
        elif word.isdigit():
            total += int(word)
            has_number = True
    return total if has_number else None

def scan_directory_deep(directory, extensions):
    file_list = []
    if not os.path.exists(directory):
        return file_list
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(extensions):
                file_list.append(os.path.join(root, file))
    file_list.sort(key=lambda x: x.lower())
    return file_list

def ask_ollama(user_text):
    current_vol = Player.audio_get_volume()
    if current_vol <= 0: 
        current_vol = 70

    system_prompt = (
        "Ты — модуль управления медиаплеером. Определи команду пользователя и верни СТРОГО JSON. "
        "Правила команд:\n"
        "1. Включить фильм/серию по названию: {\"action\": \"play\", \"target\": \"название\"}\n"
        "2. Включить по номеру: {\"action\": \"play_number\", \"number\": 5}\n"
        "3. Пауза/продолжить: {\"action\": \"pause\"}\n"
        "4. Выключить: {\"action\": \"stop\"}\n"
        "5. На весь экран/сверни: {\"action\": \"fullscreen\"}\n"
        "6. Следующий фильм: {\"action\": \"next\"}\n"
        "Формат ответа: строго JSON и ничего больше."
    )
    try:
        response = requests.post(OLLAMA_URL, json={
            "model": MODEL_NAME,
            "prompt": f"{system_prompt}\nПользователь сказал: {user_text}",
            "stream": False
        }, timeout=60)
        return response.json()['response']
    except Exception as e:
        print("\n[ОШИБКА] Нет связи с Ollama:", e)
        return None

def play_file_by_index(index):
    global current_index, current_volume
    if 0 <= index < len(current_playlist):
        current_index = index
        file_path = current_playlist[current_index]
        relative_name = os.path.relpath(file_path, MOVIES_DIR)
        print(f"\n[ПЛЕЕР] Запускаю (№{index + 1}): {relative_name}")
        
        media = Instance.media_new(file_path)
        Player.set_media(media)
        Player.play()
        Player.audio_set_volume(current_volume)
    else:
        print(f"\n[ПЛЕЕР] Ошибка: Элемента с номером {index + 1} нет в списке.")

def execute_command(ai_response):
    global current_playlist, current_index
    try:
        start = ai_response.find('{')
        end = ai_response.rfind('}') + 1
        clean_json = ai_response[start:end] if (start != -1 and end != -1) else ai_response.strip()
        
        data = json.loads(clean_json)
        action = data.get("action")
        
        movies_files = scan_directory_deep(MOVIES_DIR, ('.mp4', '.mkv', '.avi'))
        music_files = scan_directory_deep(MUSIC_DIR, ('.mp3', '.wav'))
        media_type = data.get("type", "movie") 
        
        if action == "play":
            target_name = data.get("target", "").lower()
            target_translit = russian_to_translit(target_name) # Умный фикс транслита
            
            all_files = movies_files if media_type == "movie" else music_files
            found_index = -1
            
            for i, path in enumerate(all_files):
                path_low = path.lower()
                # Ищем и по-русски, и по транслиту (на случай Doshkolnoe.obrazovanie)
                if target_name in path_low or target_translit in path_low:
                    found_index = i
                    break
            if found_index != -1:
                current_playlist = all_files
                play_file_by_index(found_index)
            else:
                print(f"\n[ИИ] Файл или серия со словом '{target_name}' не найдены.")
        elif action == "play_number":
            number = int(data.get("number", 1))
            all_files = movies_files if media_type == "movie" else music_files
            if all_files:
                current_playlist = all_files
                play_file_by_index(number - 1)
    except Exception as e:
        print("\n[ОШИБКА] Ошибка обработки команды:", e)

# ================= ОСНОВНОЙ ЦИКЛ ПРОГРАММЫ =================
if __name__ == "__main__":
    print("=== КИНО-АССИСТЕНТ OLLAMA АВТОНОМНЫЙ 5.5 (ИДЕАЛ) ЗАПУЩЕН ===")
    
    if not os.path.exists("model"):
        print("[ОШИБКА] Папка 'model' не найдена!")
        exit(1)
        
    vosk_model = Model("model")
    rec = KaldiRecognizer(vosk_model, 16000)

    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=8000)
    stream.start_stream()
    mic_active = True

    print("[СИСТЕМА] Микрофон WO Mic активен. Программа слушает комнату...\n")

    while True:
        data = stream.read(4000, exception_on_overflow=False)
        if len(data) == 0:
            continue

        if rec.AcceptWaveform(data):
            res = json.loads(rec.Result())
            raw_input = res.get("text", "")
            
            if not raw_input.strip():
                continue
                
            print(f"\n[ГОЛОС]: {raw_input}")
            
            user_command = fix_layout(raw_input)
            cmd_norm = user_command.lower().strip()
            
            # ================= БЛОК ТРИГГЕРА МИКРОФОНА =================
            # Проверка статуса (работает ВСЕГДА, даже если микрофон в режиме сна)
            if cmd_norm in ["где микрофон", "статус микрофона", "состояние микрофона"]:
                if mic_active:
                    print("\n[СТАТУС]: Микрофон АКТИВЕН")
                else:
                    print("\n[СТАТУС]: Микрофон ОТКЛЮЧЕН")
                continue

            # Если сказали "микрофон", переключаем его состояние (вкл/выкл)
            if cmd_norm in ["микрофон", "включи микрофон", "выключи микрофон", "стоп микрофон", "усни", "проснись"]:
                mic_active = not mic_active # Меняет True на False, или False на True
                
                if mic_active:
                    print("[СИСТЕМА] Микрофон СНОВА АКТИВЕН. Слушаю команды...")
                else:
                    print("[СИСТЕМА] МИКРОФОН ОТКЛЮЧЕН (РЕЖИМ СНА). Жду команду 'микрофон'...")
                continue

            # Если микрофон отправлен в сон, жестко игнорируем абсолютно всё, что написано ниже
            if not mic_active:
                continue
            # ===========================================================

            # Фильтр ложных слов от колонок (работает только когда микрофон активен)
            if any(word in cmd_norm for word in [
                "ой", "ай", "упс", "понятно", "еще", "ещё", "пошутил", "что", "завтра", "классно",
                "как дела", "потанцуем", "я могу", "танцевать", "звездой", "за свой", "не видел", "близко с", "но плохо"
            ]):
                continue

            # 1. ТОЧНАЯ ПЕРЕМОТКА
            if any(word in cmd_norm for word in ["вперед", "вперёд", "назад", "перемотай", "перемотка", "период", "перед"]):
                seconds = 30 
                extracted_num = words_to_number(cmd_norm)
                if extracted_num:
                    if "минут" in cmd_norm or "минуты" in cmd_norm or "минуту" in cmd_norm or "минутку" in cmd_norm:
                        seconds = extracted_num * 60
                    else:
                        seconds = extracted_num
                
                current_time = Player.get_time()
                if "назад" in cmd_norm:
                    Player.set_time(max(0, current_time - (seconds * 1000)))
                    print(f"[ПЛЕЕР] Перемотка НАЗАД на {seconds} сек.")
                else:
                    Player.set_time(current_time + (seconds * 1000))
                    print(f"[ПЛЕЕР] Перемотка ВПЕРЕД на {seconds} сек.")
                continue

            # 2. УПРАВЛЕНИЕ ГРОМКОСТЬЮ
            if any(word in cmd_norm for word in ["громкость", "звук", "тише", "громче", "громко"]):
                current_vol = Player.audio_get_volume()
                
                if "ноль" in cmd_norm or "выключи" in cmd_norm:
                    Player.audio_set_volume(0)
                    print("[ПЛЕЕР] Звук выключен (0%)")
                elif "тише" in cmd_norm:
                    Player.audio_set_volume(max(0, current_vol - 10))
                    print(f"[ПЛЕЕР] Громкость снижена: {Player.audio_get_volume()}%")
                elif "громче" in cmd_norm:
                    Player.audio_set_volume(min(150, current_vol + 10))
                    print(f"[ПЛЕЕР] Громкость увеличена: {Player.audio_get_volume()}%")
                else:
                    extracted_vol = words_to_number(cmd_norm)
                    if extracted_vol is not None:
                        Player.audio_set_volume(min(150, extracted_vol))
                        print(f"[ПЛЕЕР] Громкость установлена на: {Player.audio_get_volume()}%")
                continue

            # 3. БЫСТРЫЕ КОМАНДЫ ПЛЕЕРА (Пауза/Плей теперь тоже на одной кнопке!)
            if cmd_norm in ["сначала", "сначало", "на начало", "заново", "включи сначала", "начало", "начала"]:
                Player.set_position(0.0)  
                Player.play()             
                print("[ПЛЕЕР] Сброшено на начало")
                continue
                
            if any(word in cmd_norm for word in ["на весь экран", "свернуть", "полный экран", "развернуть", "экран", "на весь игра", "сверни"]):
                Player.set_fullscreen(not Player.get_fullscreen())
                print("[ПЛЕЕР] Экран переключен")
                continue

            # Объединенный плей/пауза
            if cmd_norm in ["пауза", "подожди", "включи", "плей", "продолжить", "играй", "запуск"]:
                Player.pause()
                if Player.is_playing():
                    print("[ПЛЕЕР] Пауза (Логика Python)")
                else:
                    print("[ПЛЕЕР] Воспроизведение (Логика Python)")
                continue

            if any(word == cmd_norm for word in ["следующий", "следующее", "следующая", "следующий фильм", "следующая серия", "дальше"]):
                play_file_by_index(current_index + 1)
                continue
                
            if any(word == cmd_norm or cmd_norm == "приду вещей фильм" for word in ["предыдущий", "предыдущее", "предыдущая", "предыдущий фильм", "предыдущая серия", "назад фильм", "прошлый"]):
                play_file_by_index(current_index - 1)
                continue
                
            if cmd_norm in ["выход", "стоп", "выключи", "закрой плеер"]:
                Player.stop()
                print("[ПЛЕЕР] Видео закрыто")
                continue

            if cmd_norm in ["список", "список фильмов", "что посмотреть", "файлы"]:
                files = scan_directory_deep(MOVIES_DIR, ('.mp4', '.mkv', '.avi'))
                if files:
                    print("\n=== НАЙДЕННЫЕ ФИЛЬМЫ И СЕРИАЛЫ ===")
                    for idx, path in enumerate(files):
                        print(f"[{idx + 1}] {os.path.relpath(path, MOVIES_DIR)}")
                    print("==================================\n")
                continue

            if cmd_norm in ["что за фильм", "какой это фильм", "что играет", "что за сериал", "название"]:
                if current_playlist and 0 <= current_index < len(current_playlist):
                    playing_now = os.path.relpath(current_playlist[current_index], MOVIES_DIR)
                    print(f"\n[АССИСТЕНТ] Сейчас запущено: {playing_now}")
                else:
                    print("\n[АССИСТЕНТ] Сейчас ничего не запущено.")
                continue

            # 4. ЗАПУСК ФИЛЬМА ПО НОМЕРУ (СТРОГО фильм + число)
            if any(word in cmd_norm for word in ["фильм", "номер", "серия"]):
                clean_for_num = cmd_norm.replace("фильм", "").replace("номер", "").replace("серия", "").strip()
                parsed_number = words_to_number(clean_for_num)
                
                if parsed_number and parsed_number > 0 and "минут" not in cmd_norm:
                    files = scan_directory_deep(MOVIES_DIR, ('.mp4', '.mkv', '.avi'))
                    if files and parsed_number <= len(files):
                        current_playlist = files
                        play_file_by_index(parsed_number - 1)
                        continue

            # 5. ИИ ДЛЯ ПОИСКА ПО НАЗВАНИЮ (Отработает, если не подошло ничего выше)
            if any(word in cmd_norm for word in ["включи", "поставь", "запусти", "фильм", "серия"]):
                print("[ИИ обрабатывает поиск по названию...]")
                ai_out = ask_ollama(user_command)
                if ai_out:
                    execute_command(ai_out)
           