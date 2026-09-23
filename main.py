import io
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import qrcode
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv('DATA_DIR', '/app/data'))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / 'quiz.db'
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'change-me-now')
QUESTION_TIME = int(os.getenv('QUESTION_TIME', '45'))
TOTAL_QUESTIONS = 17

QUIZ = [
    {
        'round': 1, 'title': 'ВЕРЮ — НЕ ВЕРЮ', 'type': 'bool',
        'question': 'Обогащение — это отделение полезного компонента от пустой породы.',
        'options': ['ПРАВДА', 'МИФ'], 'answer': 'ПРАВДА',
        'explanation': 'Если представить руду как смесь ценного минерала и обычной породы, задача обогащения — отделить полезное от ненужного.',
    },
    {
        'round': 1, 'title': 'ВЕРЮ — НЕ ВЕРЮ', 'type': 'bool',
        'question': 'Флотация основана на магнитных свойствах.',
        'options': ['ПРАВДА', 'МИФ'], 'answer': 'МИФ',
        'explanation': 'Флотация связана с пузырьками воздуха и реагентами. Разделение по магнитным свойствам — магнитная сепарация.',
    },
    {
        'round': 1, 'title': 'ВЕРЮ — НЕ ВЕРЮ', 'type': 'bool',
        'question': 'Концентрат — это отходы.',
        'options': ['ПРАВДА', 'МИФ'], 'answer': 'МИФ',
        'explanation': 'Концентрат содержит повышенную долю полезного компонента. Отходы после обогащения называют хвостами.',
    },
    {
        'round': 1, 'title': 'ВЕРЮ — НЕ ВЕРЮ', 'type': 'bool',
        'question': 'Вода — это H₂O.',
        'options': ['ПРАВДА', 'МИФ'], 'answer': 'ПРАВДА',
        'explanation': 'Два атома водорода и один атом кислорода: H₂O.',
    },
    {
        'round': 1, 'title': 'ВЕРЮ — НЕ ВЕРЮ', 'type': 'bool',
        'question': 'Кислород — это металл.',
        'options': ['ПРАВДА', 'МИФ'], 'answer': 'МИФ',
        'explanation': 'Кислород — неметалл и при обычных условиях находится в газообразном состоянии.',
    },
    {
        'round': 1, 'title': 'ВЕРЮ — НЕ ВЕРЮ', 'type': 'bool',
        'question': 'Поваренная соль — это NaCl.',
        'options': ['ПРАВДА', 'МИФ'], 'answer': 'ПРАВДА',
        'explanation': 'NaCl — хлорид натрия.',
    },
    {
        'round': 1, 'title': 'ВЕРЮ — НЕ ВЕРЮ', 'type': 'bool',
        'question': 'Настоящее золото мягкое, а пирит твёрдый.',
        'options': ['ПРАВДА', 'МИФ'], 'answer': 'ПРАВДА',
        'explanation': 'Золото относительно мягкое, а пирит значительно твёрже.',
    },
    {
        'round': 2, 'title': 'УГАДАЙ МЕТОД', 'type': 'choice',
        'question': 'Как называется метод обогащения, где используют магнит?',
        'options': ['Флотация', 'Магнитная сепарация', 'Грохочение', 'Промывка'],
        'answer': 'Магнитная сепарация',
        'explanation': 'Метод разделяет минералы, обладающие магнитными свойствами, и немагнитную часть материала.',
    },
    {
        'round': 2, 'title': 'УГАДАЙ МЕТОД', 'type': 'choice',
        'question': 'Как называется метод обогащения, при котором частицы руды разделяются с помощью пузырьков воздуха?',
        'options': ['Магнитная сепарация', 'Гравитация', 'Флотация', 'Дробление'],
        'answer': 'Флотация',
        'explanation': 'В пульпу добавляют реагенты, воздух образует пузырьки, а определённые минералы закрепляются на них и поднимаются с пеной.',
    },
    {
        'round': 2, 'title': 'УГАДАЙ МЕТОД', 'type': 'choice',
        'question': 'Какой метод обогащения основан на разнице плотности?',
        'options': ['Флотация', 'Гравитационное обогащение', 'Магнитная сепарация', 'Измельчение'],
        'answer': 'Гравитационное обогащение',
        'explanation': 'Тяжёлые частицы оседают, а лёгкие уносятся водой; пример — промывка золота.',
    },
    {
        'round': 2, 'title': 'УГАДАЙ МЕТОД', 'type': 'choice',
        'question': 'Как называется процесс разделения материала по крупности с помощью сит?',
        'options': ['Дробление', 'Флотация', 'Грохочение', 'Промывка'],
        'answer': 'Грохочение',
        'explanation': 'Крупные куски остаются сверху, мелкие проходят через отверстия сит; на фабриках используют грохоты.',
    },
    {
        'round': 2, 'title': 'УГАДАЙ МЕТОД', 'type': 'choice',
        'question': 'Как называется процесс уменьшения размеров кусков руды?',
        'options': ['Грохочение', 'Флотация', 'Дробление и измельчение', 'Магнитная сепарация'],
        'answer': 'Дробление и измельчение',
        'explanation': 'Сначала куски руды уменьшают в размерах, чтобы раскрыть минеральные зёрна и затем их разделить.',
    },
    {
        'round': 3, 'title': 'ВПИШИ СЛОВО', 'type': 'text',
        'question': 'Какой полезный металл добывают из минерала магнетита?', 'options': [],
        'answer': 'ЖЕЛЕЗО', 'aliases': ['железо'],
        'explanation': 'Магнетит — важный железорудный минерал.',
    },
    {
        'round': 3, 'title': 'ВПИШИ СЛОВО', 'type': 'text',
        'question': 'За схожесть с золотом этот минерал прозвали “золотом дураков”. Что это?', 'options': [],
        'answer': 'ПИРИТ', 'aliases': ['пирит'],
        'explanation': 'Пирит может напоминать золото по цвету и блеску.',
    },
    {
        'round': 3, 'title': 'ВПИШИ СЛОВО', 'type': 'text',
        'question': 'Самая твёрдая природная форма углерода. Используется в ювелирной промышленности и для обработки очень твёрдых материалов.', 'options': [],
        'answer': 'АЛМАЗ', 'aliases': ['алмаз'],
        'explanation': 'Алмаз имеет твёрдость 10 по шкале Мооса; алмаз и графит состоят из углерода, но имеют разное строение кристаллической решётки.',
    },
    {
        'round': 3, 'title': 'ВПИШИ СЛОВО', 'type': 'text',
        'question': 'Как называется простой инструмент старателей для поиска и промывки золота?', 'options': [],
        'answer': 'ЛОТОК', 'aliases': ['лоток', 'золотопромывочный лоток'],
        'explanation': 'Золото плотнее обычного песка, поэтому при промывке оно остаётся в лотке.',
    },
    {
        'round': 3, 'title': 'ВПИШИ СЛОВО', 'type': 'text',
        'question': 'В сердце Забайкалья есть город, известный как крупный центр добычи стратегического сырья. Что там добывают?', 'options': [],
        'answer': 'УРАН', 'aliases': ['уран'],
        'explanation': 'В исходном сценарии правильный ответ указан как «уран».',
    },
]

REACTIONS = ['👍', '😊', '❤️', '🔥']

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title='Алхимик — интерактивный квиз', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=BASE_DIR / 'static'), name='static')
templates = Jinja2Templates(directory=BASE_DIR / 'templates')


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS state (
            id INTEGER PRIMARY KEY CHECK (id=1),
            room_code TEXT NOT NULL,
            phase TEXT NOT NULL,
            q_index INTEGER NOT NULL,
            deadline REAL NOT NULL,
            started_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS teams (
            team_code TEXT PRIMARY KEY,
            team_name TEXT NOT NULL,
            score INTEGER NOT NULL DEFAULT 0,
            joined_at REAL NOT NULL,
            last_seen REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS answers (
            team_code TEXT NOT NULL,
            q_index INTEGER NOT NULL,
            value TEXT NOT NULL,
            correct INTEGER NOT NULL,
            submitted_at REAL NOT NULL,
            PRIMARY KEY(team_code, q_index)
        );
        CREATE TABLE IF NOT EXISTS reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_code TEXT,
            emoji TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        ''')
        # Migrate old installations created by v3.
        cols = {r['name'] for r in conn.execute('PRAGMA table_info(teams)').fetchall()}
        if 'last_seen' not in cols:
            conn.execute('ALTER TABLE teams ADD COLUMN last_seen REAL NOT NULL DEFAULT 0')
        state = conn.execute('SELECT id FROM state WHERE id=1').fetchone()
        if not state:
            conn.execute(
                'INSERT INTO state(id,room_code,phase,q_index,deadline,started_at) VALUES(1,?,?,?,?,?)',
                (make_room_code(), 'waiting', -1, 0, time.time()),
            )


def make_room_code() -> str:
    return f"{secrets.randbelow(900000) + 100000}"


def get_state() -> dict[str, Any]:
    init_db()
    with db() as conn:
        row = conn.execute('SELECT * FROM state WHERE id=1').fetchone()
        state = dict(row)
        if state['phase'] == 'question' and time.time() >= state['deadline']:
            conn.execute('UPDATE state SET phase=? WHERE id=1', ('reveal',))
            state['phase'] = 'reveal'
    return state


def normalize(text: str) -> str:
    return ' '.join(text.strip().lower().replace('ё', 'е').split())


def is_correct(q: dict[str, Any], value: str) -> bool:
    norm = normalize(value)
    answers = [normalize(q['answer'])] + [normalize(a) for a in q.get('aliases', [])]
    return norm in answers


def public_question(state: dict[str, Any], include_answer: bool = False) -> dict[str, Any] | None:
    idx = state['q_index']
    if idx < 0 or idx >= len(QUIZ):
        return None
    q = QUIZ[idx].copy()
    q.pop('aliases', None)
    if not include_answer:
        q.pop('answer', None)
        q.pop('explanation', None)
    return q


def leaderboard() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute('''
            SELECT team_code, team_name, score, joined_at, last_seen
            FROM teams ORDER BY score DESC, team_name COLLATE NOCASE ASC
        ''').fetchall()
    return [dict(r) for r in rows]


def participants() -> list[dict[str, Any]]:
    now = time.time()
    result = []
    for i, row in enumerate(leaderboard(), 1):
        row['rank'] = i
        row['online'] = (now - row['last_seen']) < 8
        result.append(row)
    return result


def check_admin(request: Request):
    if request.cookies.get('admin_ok') != '1':
        return JSONResponse({'ok': False, 'message': 'Требуется вход в админку.'}, status_code=401)
    return None


@app.get('/health')
async def health():
    return JSONResponse({'ok': True, 'version': 'bothost-v4'})


@app.get('/', response_class=HTMLResponse)
async def index(request: Request):
    state = get_state()
    return templates.TemplateResponse('index.html', {'request': request, 'state': state, 'question_time': QUESTION_TIME})


@app.get('/admin', response_class=HTMLResponse)
async def admin(request: Request):
    if request.cookies.get('admin_ok') != '1':
        return templates.TemplateResponse('admin_login.html', {'request': request})
    return templates.TemplateResponse('admin.html', {'request': request, 'question_time': QUESTION_TIME, 'question_count': TOTAL_QUESTIONS})


@app.post('/admin/login')
async def admin_login(password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        return HTMLResponse('Неверный пароль', status_code=401)
    response = RedirectResponse('/admin', status_code=303)
    response.set_cookie('admin_ok', '1', httponly=True, samesite='lax')
    return response


@app.post('/api/admin/new')
async def admin_new(request: Request):
    err = check_admin(request)
    if err:
        return err
    with db() as conn:
        conn.execute('DELETE FROM teams')
        conn.execute('DELETE FROM answers')
        conn.execute('DELETE FROM reactions')
        conn.execute('UPDATE state SET room_code=?,phase=?,q_index=?,deadline=?,started_at=? WHERE id=1',
                     (make_room_code(), 'waiting', -1, 0, time.time()))
        state = dict(conn.execute('SELECT * FROM state WHERE id=1').fetchone())
    return JSONResponse({'ok': True, 'state': state})


@app.post('/api/admin/start')
async def admin_start(request: Request):
    err = check_admin(request)
    if err:
        return err
    state = get_state()
    # Starting from waiting launches question 1 without deleting participants.
    if state['phase'] not in ('waiting',):
        return JSONResponse({'ok': False, 'message': 'Квиз уже запущен. Используйте «Начать заново».'.encode().decode()}, status_code=400)
    now = time.time()
    with db() as conn:
        conn.execute('DELETE FROM answers')
        conn.execute('UPDATE teams SET score=0')
        conn.execute('UPDATE state SET phase=?,q_index=?,deadline=?,started_at=? WHERE id=1',
                     ('question', 0, now + QUESTION_TIME, now))
    return JSONResponse({'ok': True})


@app.post('/api/admin/reveal')
async def admin_reveal(request: Request):
    err = check_admin(request)
    if err:
        return err
    state = get_state()
    if state['phase'] != 'question':
        return JSONResponse({'ok': False, 'message': 'Сейчас нечего открывать.'}, status_code=400)
    with db() as conn:
        conn.execute('UPDATE state SET phase=? WHERE id=1', ('reveal',))
    return JSONResponse({'ok': True})


@app.post('/api/admin/next')
async def admin_next(request: Request):
    err = check_admin(request)
    if err:
        return err
    state = get_state()
    if state['phase'] == 'question':
        with db() as conn:
            conn.execute('UPDATE state SET phase=? WHERE id=1', ('reveal',))
        return JSONResponse({'ok': True, 'phase': 'reveal'})
    if state['phase'] != 'reveal':
        return JSONResponse({'ok': False, 'message': 'Сначала запустите вопрос.'}, status_code=400)
    next_idx = state['q_index'] + 1
    if next_idx >= TOTAL_QUESTIONS:
        with db() as conn:
            conn.execute('UPDATE state SET phase=?,q_index=?,deadline=? WHERE id=1', ('finished', TOTAL_QUESTIONS, 0))
        return JSONResponse({'ok': True, 'phase': 'finished'})
    now = time.time()
    with db() as conn:
        conn.execute('UPDATE state SET phase=?,q_index=?,deadline=? WHERE id=1', ('question', next_idx, now + QUESTION_TIME))
    return JSONResponse({'ok': True, 'phase': 'question', 'q_index': next_idx})


@app.post('/api/admin/finish')
async def admin_finish(request: Request):
    err = check_admin(request)
    if err:
        return err
    with db() as conn:
        conn.execute('UPDATE state SET phase=?,q_index=?,deadline=? WHERE id=1', ('finished', TOTAL_QUESTIONS, 0))
    return JSONResponse({'ok': True})


@app.post('/api/admin/reset')
async def admin_reset(request: Request):
    return await admin_new(request)


@app.get('/api/state')
async def api_state():
    state = get_state()
    include_answer = state['phase'] in ('reveal', 'finished')
    return JSONResponse({
        'state': state,
        'question': public_question(state, include_answer=include_answer),
        'question_time': QUESTION_TIME,
        'total_questions': TOTAL_QUESTIONS,
        'leaderboard': leaderboard(),
    })


@app.get('/api/scores')
async def api_scores():
    return JSONResponse({'teams': leaderboard()})


@app.get('/api/participants')
async def api_participants(request: Request):
    err = check_admin(request)
    if err:
        return err
    return JSONResponse({'participants': participants()})


@app.get('/api/reactions')
async def api_reactions(since: int = 0):
    with db() as conn:
        rows = conn.execute('SELECT id, emoji, created_at FROM reactions WHERE id>? ORDER BY id ASC LIMIT 100', (since,)).fetchall()
    return JSONResponse({'reactions': [dict(r) for r in rows]})


@app.get('/api/qr')
async def api_qr(code: str):
    url = f"/play?code={code}"
    # Resolve relative URL for public QR readers.
    # The caller can pass an absolute join URL as code parameter if desired.
    if code.startswith('http://') or code.startswith('https://'):
        url = code
    qr = qrcode.QRCode(version=None, box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color='black', back_color='white')
    buf = io.BytesIO()
    image.save(buf, format='PNG')
    buf.seek(0)
    return StreamingResponse(buf, media_type='image/png', headers={'Cache-Control': 'no-store'})


@app.post('/api/reaction')
async def reaction(team_code: str = Form(''), emoji: str = Form(...)):
    if emoji not in REACTIONS:
        return JSONResponse({'ok': False, 'message': 'Неизвестная реакция.'}, status_code=400)
    with db() as conn:
        conn.execute('INSERT INTO reactions(team_code,emoji,created_at) VALUES(?,?,?)', (team_code or None, emoji, time.time()))
    return JSONResponse({'ok': True})


@app.get('/screen', response_class=HTMLResponse)
async def screen(request: Request):
    return templates.TemplateResponse('screen.html', {'request': request, 'question_time': QUESTION_TIME, 'question_count': TOTAL_QUESTIONS, 'reactions': REACTIONS})


@app.get('/play', response_class=HTMLResponse)
async def play(request: Request):
    return templates.TemplateResponse('play.html', {'request': request, 'question_time': QUESTION_TIME, 'question_count': TOTAL_QUESTIONS, 'reactions': REACTIONS})


@app.post('/api/join')
async def join(room_code: str = Form(...), team_name: str = Form(...)):
    room_code = room_code.strip()
    team_name = team_name.strip()
    state = get_state()
    if room_code != state['room_code']:
        return JSONResponse({'ok': False, 'message': 'Неверный код комнаты.'}, status_code=400)
    if not team_name:
        return JSONResponse({'ok': False, 'message': 'Введите название команды.'}, status_code=400)
    if len(team_name) > 40:
        return JSONResponse({'ok': False, 'message': 'Название слишком длинное.'}, status_code=400)
    now = time.time()
    with db() as conn:
        existing = conn.execute('SELECT team_code FROM teams WHERE team_name=?', (team_name,)).fetchone()
        if existing:
            team_code = existing['team_code']
            conn.execute('UPDATE teams SET last_seen=? WHERE team_code=?', (now, team_code))
        else:
            team_code = secrets.token_hex(4).upper()
            conn.execute('INSERT INTO teams(team_code,team_name,score,joined_at,last_seen) VALUES(?,?,?,?,?)',
                         (team_code, team_name, 0, now, now))
    return JSONResponse({'ok': True, 'team_code': team_code, 'team_name': team_name, 'room_code': room_code})


@app.get('/api/team/{team_code}')
async def team_state(team_code: str):
    state = get_state()
    with db() as conn:
        conn.execute('UPDATE teams SET last_seen=? WHERE team_code=?', (time.time(), team_code))
        team = conn.execute('SELECT team_code,team_name,score FROM teams WHERE team_code=?', (team_code,)).fetchone()
        if not team:
            return JSONResponse({'ok': False, 'message': 'Команда не найдена.'}, status_code=404)
        current_answer = conn.execute('SELECT * FROM answers WHERE team_code=? AND q_index=?', (team_code, state['q_index'])).fetchone()
    answered = current_answer is not None
    result = None
    if current_answer and state['phase'] in ('reveal', 'finished'):
        q = QUIZ[state['q_index']] if state['q_index'] < TOTAL_QUESTIONS else None
        result = {
            'submitted': current_answer['value'],
            'correct': bool(current_answer['correct']),
            'correct_answer': q['answer'] if q else None,
            'explanation': q.get('explanation', '') if q else '',
        }
    include_answer = state['phase'] in ('reveal', 'finished')
    return JSONResponse({
        'ok': True,
        'state': state,
        'question': public_question(state, include_answer=include_answer),
        'team': dict(team),
        'answered': answered,
        'result': result,
        'leaderboard': leaderboard(),
    })


@app.post('/api/answer')
async def answer(team_code: str = Form(...), q_index: int = Form(...), value: str = Form(...)):
    state = get_state()
    if state['phase'] != 'question':
        return JSONResponse({'ok': False, 'message': 'Приём ответов закрыт. Дождитесь следующего вопроса.'}, status_code=400)
    if q_index != state['q_index'] or time.time() >= state['deadline']:
        return JSONResponse({'ok': False, 'message': 'Время на этот вопрос истекло.'}, status_code=400)
    if q_index < 0 or q_index >= TOTAL_QUESTIONS:
        return JSONResponse({'ok': False, 'message': 'Некорректный вопрос.'}, status_code=400)
    value = value.strip()
    if not value:
        return JSONResponse({'ok': False, 'message': 'Введите или выберите ответ.'}, status_code=400)
    correct = is_correct(QUIZ[q_index], value)
    now = time.time()
    with db() as conn:
        team = conn.execute('SELECT team_code FROM teams WHERE team_code=?', (team_code,)).fetchone()
        if not team:
            return JSONResponse({'ok': False, 'message': 'Команда не найдена.'}, status_code=404)
        exists = conn.execute('SELECT 1 FROM answers WHERE team_code=? AND q_index=?', (team_code, q_index)).fetchone()
        if exists:
            return JSONResponse({'ok': False, 'message': 'Ответ уже принят.'}, status_code=400)
        conn.execute('INSERT INTO answers(team_code,q_index,value,correct,submitted_at) VALUES(?,?,?,?,?)',
                     (team_code, q_index, value, int(correct), now))
        if correct:
            conn.execute('UPDATE teams SET score=score+1,last_seen=? WHERE team_code=?', (now, team_code))
        else:
            conn.execute('UPDATE teams SET last_seen=? WHERE team_code=?', (now, team_code))
        score = conn.execute('SELECT score FROM teams WHERE team_code=?', (team_code,)).fetchone()['score']
    return JSONResponse({'ok': True, 'correct': correct, 'score': score})
