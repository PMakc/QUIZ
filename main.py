import io
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import qrcode
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv('DATA_DIR', '/app/data'))
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR = DATA_DIR / 'uploads'
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / 'quiz.db'
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'change-me-now')
DEFAULT_QUESTION_TIME = int(os.getenv('QUESTION_TIME', '45'))
ALLOWED_IMAGE_TYPES = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/gif': '.gif'}
REACTIONS = ['👍', '😊', '❤️', '🔥']
QUESTION_TYPES = ['choice', 'multiple', 'bool', 'text']

app = FastAPI(title='Алхимик — конструктор квиза')
app.mount('/static', StaticFiles(directory=BASE_DIR / 'static'), name='static')
app.mount('/media', StaticFiles(directory=UPLOAD_DIR), name='media')
templates = Jinja2Templates(directory=BASE_DIR / 'templates')


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def now() -> float:
    return time.time()


def make_code() -> str:
    return f'{secrets.randbelow(900000) + 100000}'


def normalize(text: str) -> str:
    return ' '.join((text or '').strip().lower().replace('ё', 'е').split())


def check_admin(request: Request):
    if request.cookies.get('admin_ok') != '1':
        return JSONResponse({'ok': False, 'message': 'Требуется вход в админку.'}, status_code=401)
    return None


def init_db():
    with db() as conn:
        # Migrate the previous v5 schema before creating v6 tables.
        state_cols = {r['name'] for r in conn.execute("PRAGMA table_info(state)").fetchall()}
        if state_cols and 'current_question_id' not in state_cols:
            conn.execute('ALTER TABLE state RENAME TO state_legacy_v5')
        answer_cols = {r['name'] for r in conn.execute("PRAGMA table_info(answers)").fetchall()}
        if answer_cols and 'question_id' not in answer_cols:
            conn.execute('ALTER TABLE answers RENAME TO answers_legacy_v5')
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY CHECK(id=1),
            title TEXT NOT NULL,
            subtitle TEXT NOT NULL,
            question_time INTEGER NOT NULL DEFAULT 45
        );
        CREATE TABLE IF NOT EXISTS rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position INTEGER NOT NULL,
            title TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            type TEXT NOT NULL,
            text TEXT NOT NULL,
            image TEXT,
            options_json TEXT NOT NULL DEFAULT '[]',
            answer_json TEXT NOT NULL DEFAULT '[]',
            aliases_json TEXT NOT NULL DEFAULT '[]',
            explanation TEXT NOT NULL DEFAULT '',
            points INTEGER NOT NULL DEFAULT 1,
            time_limit INTEGER,
            CHECK(type IN ('choice','multiple','bool','text'))
        );
        CREATE TABLE IF NOT EXISTS state (
            id INTEGER PRIMARY KEY CHECK(id=1),
            room_code TEXT NOT NULL,
            phase TEXT NOT NULL,
            current_question_id INTEGER,
            deadline REAL NOT NULL DEFAULT 0,
            started_at REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS teams (
            team_code TEXT PRIMARY KEY,
            team_name TEXT NOT NULL UNIQUE,
            score INTEGER NOT NULL DEFAULT 0,
            joined_at REAL NOT NULL,
            last_seen REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS answers (
            team_code TEXT NOT NULL,
            question_id INTEGER NOT NULL,
            value_json TEXT NOT NULL,
            correct INTEGER NOT NULL,
            points_awarded INTEGER NOT NULL DEFAULT 0,
            submitted_at REAL NOT NULL,
            PRIMARY KEY(team_code, question_id),
            FOREIGN KEY(team_code) REFERENCES teams(team_code) ON DELETE CASCADE,
            FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_code TEXT,
            emoji TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_questions_round_pos ON questions(round_id, position);
        CREATE INDEX IF NOT EXISTS idx_answers_question ON answers(question_id);
        CREATE INDEX IF NOT EXISTS idx_reactions_id ON reactions(id);
        ''')
        if not conn.execute('SELECT 1 FROM settings WHERE id=1').fetchone():
            conn.execute('INSERT INTO settings(id,title,subtitle,question_time) VALUES(1,?,?,?)',
                         ('Алхимик', 'Синдром Эфирита', DEFAULT_QUESTION_TIME))
        if not conn.execute('SELECT 1 FROM rounds').fetchone():
            seed_rounds = [('ВЕРЮ — НЕ ВЕРЮ', 1), ('УГАДАЙ МЕТОД', 2), ('ВПИШИ СЛОВО', 3)]
            for title, pos in seed_rounds:
                conn.execute('INSERT INTO rounds(position,title) VALUES(?,?)', (pos, title))
            seed = seed_questions()
            round_map = {r['position']: r['id'] for r in conn.execute('SELECT id,position FROM rounds')}
            for q in seed:
                conn.execute('''INSERT INTO questions(round_id,position,type,text,image,options_json,answer_json,aliases_json,explanation,points,time_limit)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                             (round_map[q['round']], q['position'], q['type'], q['text'], None,
                              json.dumps(q['options'], ensure_ascii=False), json.dumps(q['answer'], ensure_ascii=False),
                              json.dumps(q.get('aliases', []), ensure_ascii=False), q.get('explanation', ''), 1, None))
        if not conn.execute('SELECT 1 FROM state WHERE id=1').fetchone():
            legacy_code = None
            if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='state_legacy_v5'").fetchone():
                legacy = conn.execute('SELECT room_code FROM state_legacy_v5 WHERE id=1').fetchone()
                legacy_code = legacy['room_code'] if legacy else None
            conn.execute('INSERT INTO state(id,room_code,phase,current_question_id,deadline,started_at) VALUES(1,?,?,?,?,?)',
                         (legacy_code or make_code(), 'waiting', None, 0, 0))
        # Repair any old malformed phase.
        conn.execute("UPDATE state SET phase='waiting' WHERE phase NOT IN ('waiting','question','reveal','finished')")
        conn.commit()


def seed_questions():
    return [
        {'round':1,'position':1,'type':'bool','text':'Обогащение — это отделение полезного компонента от пустой породы.','options':['ПРАВДА','МИФ'],'answer':'ПРАВДА','explanation':'Задача обогащения — отделить полезный компонент от пустой породы.'},
        {'round':1,'position':2,'type':'bool','text':'Флотация основана на магнитных свойствах.','options':['ПРАВДА','МИФ'],'answer':'МИФ','explanation':'Флотация связана с пузырьками воздуха и реагентами; магнитная сепарация — отдельный метод.'},
        {'round':1,'position':3,'type':'bool','text':'Концентрат — это отходы.','options':['ПРАВДА','МИФ'],'answer':'МИФ','explanation':'Концентрат содержит повышенную долю полезного компонента.'},
        {'round':1,'position':4,'type':'bool','text':'Вода — это H₂O.','options':['ПРАВДА','МИФ'],'answer':'ПРАВДА','explanation':'Два атома водорода и один атом кислорода.'},
        {'round':1,'position':5,'type':'bool','text':'Кислород — это металл.','options':['ПРАВДА','МИФ'],'answer':'МИФ','explanation':'Кислород — неметалл.'},
        {'round':1,'position':6,'type':'bool','text':'Поваренная соль — это NaCl.','options':['ПРАВДА','МИФ'],'answer':'ПРАВДА','explanation':'NaCl — хлорид натрия.'},
        {'round':1,'position':7,'type':'bool','text':'Настоящее золото мягкое, а пирит твёрдый.','options':['ПРАВДА','МИФ'],'answer':'ПРАВДА','explanation':'Золото относительно мягкое, а пирит значительно твёрже.'},
        {'round':2,'position':1,'type':'choice','text':'Как называется метод обогащения, где используют магнит?','options':['Флотация','Магнитная сепарация','Грохочение','Промывка'],'answer':'Магнитная сепарация','explanation':'Разделение минералов по магнитным свойствам.'},
        {'round':2,'position':2,'type':'choice','text':'Как называется метод обогащения, при котором частицы руды разделяются с помощью пузырьков воздуха?','options':['Магнитная сепарация','Гравитация','Флотация','Дробление'],'answer':'Флотация','explanation':'Минералы закрепляются на пузырьках воздуха и поднимаются с пеной.'},
        {'round':2,'position':3,'type':'choice','text':'Какой метод обогащения основан на разнице плотности?','options':['Флотация','Гравитационное обогащение','Магнитная сепарация','Измельчение'],'answer':'Гравитационное обогащение','explanation':'Тяжёлые частицы оседают, лёгкие уносятся водой.'},
        {'round':2,'position':4,'type':'choice','text':'Как называется процесс разделения материала по крупности с помощью сит?','options':['Дробление','Флотация','Грохочение','Промывка'],'answer':'Грохочение','explanation':'Материал разделяется по крупности через отверстия сит.'},
        {'round':2,'position':5,'type':'choice','text':'Как называется процесс уменьшения размеров кусков руды?','options':['Грохочение','Флотация','Дробление и измельчение','Магнитная сепарация'],'answer':'Дробление и измельчение','explanation':'Куски руды уменьшают в размерах, раскрывая минеральные зёрна.'},
        {'round':3,'position':1,'type':'text','text':'Какой полезный металл добывают из минерала магнетита?','options':[],'answer':'ЖЕЛЕЗО','aliases':['железо'],'explanation':'Магнетит — важный железорудный минерал.'},
        {'round':3,'position':2,'type':'text','text':'За схожесть с золотом этот минерал прозвали «золотом дураков». Что это?','options':[],'answer':'ПИРИТ','aliases':['пирит'],'explanation':'Пирит может напоминать золото по цвету и блеску.'},
        {'round':3,'position':3,'type':'text','text':'Самая твёрдая природная форма углерода. Используется в ювелирной промышленности и для обработки очень твёрдых материалов.','options':[],'answer':'АЛМАЗ','aliases':['алмаз'],'explanation':'Алмаз и графит состоят из углерода, но имеют разное строение решётки.'},
        {'round':3,'position':4,'type':'text','text':'Как называется простой инструмент старателей для поиска и промывки золота?','options':[],'answer':'ЛОТОК','aliases':['лоток','золотопромывочный лоток'],'explanation':'Золото плотнее песка, поэтому остаётся в лотке при промывке.'},
        {'round':3,'position':5,'type':'text','text':'В сердце Забайкалья есть город, известный как крупный центр добычи стратегического сырья. Что там добывают?','options':[],'answer':'УРАН','aliases':['уран'],'explanation':'В исходном сценарии правильный ответ указан как «уран».'},
    ]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app.router.lifespan_context = lifespan


def get_settings() -> dict[str, Any]:
    with db() as conn:
        return dict(conn.execute('SELECT * FROM settings WHERE id=1').fetchone())


def list_rounds(with_questions=True):
    with db() as conn:
        rounds = [dict(r) for r in conn.execute('SELECT * FROM rounds ORDER BY position,id').fetchall()]
        if with_questions:
            for r in rounds:
                qs = conn.execute('''SELECT * FROM questions WHERE round_id=? ORDER BY position,id''', (r['id'],)).fetchall()
                r['questions'] = [question_dict(q, public=False) for q in qs]
                r['question_count'] = len(r['questions'])
        return rounds


def question_dict(row: sqlite3.Row | dict, public=True) -> dict[str, Any]:
    d = dict(row)
    d['options'] = json.loads(d.pop('options_json') or '[]')
    answers = json.loads(d.pop('answer_json') or '[]')
    if d['type'] == 'multiple':
        d['answer'] = answers
    else:
        d['answer'] = answers[0] if answers else ''
    d['aliases'] = json.loads(d.pop('aliases_json') or '[]')
    if public:
        d.pop('aliases', None)
        d.pop('answer', None)
        d.pop('explanation', None)
    return d


def all_questions() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute('''SELECT q.*, r.position AS round_position, r.title AS round_title
                              FROM questions q JOIN rounds r ON r.id=q.round_id
                              ORDER BY r.position,q.position,q.id''').fetchall()
    return [question_dict(r, public=False) for r in rows]


def current_state() -> dict[str, Any]:
    init_db()
    with db() as conn:
        s = dict(conn.execute('SELECT * FROM state WHERE id=1').fetchone())
        if s['phase'] == 'question' and s['deadline'] and now() >= s['deadline']:
            conn.execute('UPDATE state SET phase=? WHERE id=1', ('reveal',))
            s['phase'] = 'reveal'
    return s


def current_question_full() -> dict[str, Any] | None:
    s = current_state()
    if not s['current_question_id']:
        return None
    with db() as conn:
        row = conn.execute('''SELECT q.*, r.position AS round_position, r.title AS round_title
                              FROM questions q JOIN rounds r ON r.id=q.round_id WHERE q.id=?''', (s['current_question_id'],)).fetchone()
    return question_dict(row, public=False) if row else None


def public_current_question(include_answer=False):
    q = current_question_full()
    if not q:
        return None
    if include_answer:
        return q
    q = dict(q)
    q.pop('answer', None); q.pop('aliases', None); q.pop('explanation', None)
    return q


def leaderboard():
    with db() as conn:
        return [dict(r) for r in conn.execute('''SELECT team_code,team_name,score,joined_at,last_seen FROM teams
          ORDER BY score DESC, team_name COLLATE NOCASE ASC''').fetchall()]


def participants():
    t = now(); out=[]
    for i, row in enumerate(leaderboard(), 1):
        row['rank']=i; row['online']=(t-row['last_seen'])<8
        out.append(row)
    return out


def parse_answer(q: dict[str, Any], value: str):
    if q['type'] == 'multiple':
        try:
            vals = json.loads(value)
            if not isinstance(vals, list):
                raise ValueError
            return [str(v) for v in vals]
        except Exception:
            return [value]
    return value.strip()


def evaluate(q: dict[str, Any], value: str) -> bool:
    got = parse_answer(q, value)
    if q['type'] == 'multiple':
        correct = {normalize(x) for x in q['answer']}
        return {normalize(x) for x in got} == correct
    return normalize(str(got)) == normalize(str(q['answer'])) or normalize(str(got)) in {normalize(x) for x in q.get('aliases', [])}


@app.get('/health')
async def health():
    return JSONResponse({'ok': True, 'version': 'bothost-v6'})

@app.get('/favicon.ico')
async def favicon():
    return JSONResponse({}, status_code=204)

@app.get('/', response_class=HTMLResponse)
async def index(request: Request):
    s=get_settings()
    return templates.TemplateResponse(request=request, name='index.html', context={'settings':s})

@app.get('/admin', response_class=HTMLResponse)
async def admin(request: Request):
    if request.cookies.get('admin_ok') != '1':
        return templates.TemplateResponse(request=request, name='admin_login.html', context={})
    s=get_settings()
    return templates.TemplateResponse(request=request, name='admin.html', context={'settings':s})

@app.get('/screen', response_class=HTMLResponse)
async def screen(request: Request):
    s=get_settings()
    return templates.TemplateResponse(request=request, name='screen.html', context={'settings':s, 'reactions':REACTIONS})

@app.get('/play', response_class=HTMLResponse)
async def play(request: Request):
    s=get_settings()
    return templates.TemplateResponse(request=request, name='play.html', context={'settings':s, 'reactions':REACTIONS})

@app.post('/admin/login')
async def admin_login(password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        return HTMLResponse('Неверный пароль', status_code=401)
    response=RedirectResponse('/admin', status_code=303)
    response.set_cookie('admin_ok','1',httponly=True,samesite='lax')
    return response

@app.get('/api/state')
async def api_state():
    s=current_state(); q=public_current_question(include_answer=s['phase'] in ('reveal','finished')); settings=get_settings()
    return JSONResponse({'state':s,'question':q,'settings':settings,'leaderboard':leaderboard()})

@app.get('/api/scores')
async def scores():
    return JSONResponse({'teams':leaderboard()})

@app.get('/api/participants')
async def api_participants(request: Request):
    err=check_admin(request)
    if err:return err
    return JSONResponse({'participants':participants()})

@app.get('/api/admin/config')
async def admin_config(request: Request):
    err=check_admin(request)
    if err:return err
    return JSONResponse({'settings':get_settings(),'rounds':list_rounds()})

@app.post('/api/admin/settings')
async def admin_settings(request: Request, title: str=Form(...), subtitle: str=Form(''), question_time: int=Form(45)):
    err=check_admin(request)
    if err:return err
    question_time=max(5,min(question_time,300))
    with db() as conn:
        conn.execute('UPDATE settings SET title=?,subtitle=?,question_time=? WHERE id=1',(title.strip() or 'Квиз',subtitle.strip(),question_time))
    return JSONResponse({'ok':True,'settings':get_settings()})

@app.post('/api/admin/rounds')
async def add_round(request: Request, title: str=Form(...)):
    err=check_admin(request)
    if err:return err
    title=title.strip() or 'Новый раунд'
    with db() as conn:
        pos=(conn.execute('SELECT COALESCE(MAX(position),0)+1 FROM rounds').fetchone()[0])
        rid=conn.execute('INSERT INTO rounds(position,title) VALUES(?,?)',(pos,title)).lastrowid
    return JSONResponse({'ok':True,'round_id':rid})

@app.post('/api/admin/rounds/{round_id}')
async def edit_round(request: Request, round_id: int, title: str=Form(...)):
    err=check_admin(request)
    if err:return err
    with db() as conn:
        conn.execute('UPDATE rounds SET title=? WHERE id=?',(title.strip() or 'Раунд',round_id))
    return JSONResponse({'ok':True})

@app.delete('/api/admin/rounds/{round_id}')
async def delete_round(request: Request, round_id: int):
    err=check_admin(request)
    if err:return err
    with db() as conn:
        cnt=conn.execute('SELECT COUNT(*) FROM rounds').fetchone()[0]
        if cnt<=1:
            return JSONResponse({'ok':False,'message':'Должен остаться хотя бы один раунд.'},status_code=400)
        conn.execute('DELETE FROM rounds WHERE id=?',(round_id,))
    return JSONResponse({'ok':True})

@app.post('/api/admin/questions')
async def add_question(request: Request, round_id: int=Form(...), q_type: str=Form('choice'), text: str=Form(...), options: str=Form('[]'), answer: str=Form(''), aliases: str=Form('[]'), explanation: str=Form(''), points: int=Form(1), time_limit: int=Form(0), image: UploadFile|None=File(None)):
    err=check_admin(request)
    if err:return err
    if q_type not in QUESTION_TYPES:return JSONResponse({'ok':False,'message':'Неизвестный тип вопроса.'},status_code=400)
    try: opts=json.loads(options)
    except: opts=[x.strip() for x in options.split('\n') if x.strip()]
    if not isinstance(opts,list):opts=[]
    if q_type=='multiple':
        try: ans=json.loads(answer)
        except: ans=[x.strip() for x in answer.split('\n') if x.strip()]
        if not isinstance(ans,list): ans=[str(ans)]
    else: ans=[answer.strip()]
    try: als=json.loads(aliases)
    except: als=[x.strip() for x in aliases.split('\n') if x.strip()]
    filename=None
    if image and image.filename:
        if image.content_type not in ALLOWED_IMAGE_TYPES:return JSONResponse({'ok':False,'message':'Допустимы JPG, PNG, WEBP или GIF.'},status_code=400)
        filename=f'{uuid.uuid4().hex}{ALLOWED_IMAGE_TYPES[image.content_type]}'
        (UPLOAD_DIR/filename).write_bytes(await image.read())
    with db() as conn:
        pos=conn.execute('SELECT COALESCE(MAX(position),0)+1 FROM questions WHERE round_id=?',(round_id,)).fetchone()[0]
        qid=conn.execute('''INSERT INTO questions(round_id,position,type,text,image,options_json,answer_json,aliases_json,explanation,points,time_limit)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(round_id,pos,q_type,text.strip(),filename,json.dumps(opts,ensure_ascii=False),json.dumps(ans,ensure_ascii=False),json.dumps(als,ensure_ascii=False),explanation.strip(),max(1,points),time_limit or None)).lastrowid
    return JSONResponse({'ok':True,'question_id':qid})

@app.post('/api/admin/questions/{question_id}')
async def edit_question(request: Request, question_id: int, round_id: int=Form(...), q_type: str=Form(...), text: str=Form(...), options: str=Form('[]'), answer: str=Form(''), aliases: str=Form('[]'), explanation: str=Form(''), points: int=Form(1), time_limit: int=Form(0), image: UploadFile|None=File(None), remove_image: bool=Form(False)):
    err=check_admin(request)
    if err:return err
    if q_type not in QUESTION_TYPES:return JSONResponse({'ok':False,'message':'Неизвестный тип вопроса.'},status_code=400)
    try: opts=json.loads(options)
    except: opts=[x.strip() for x in options.split('\n') if x.strip()]
    if q_type=='multiple':
        try: ans=json.loads(answer)
        except: ans=[x.strip() for x in answer.split('\n') if x.strip()]
        if not isinstance(ans,list):ans=[str(ans)]
    else: ans=[answer.strip()]
    try: als=json.loads(aliases)
    except: als=[x.strip() for x in aliases.split('\n') if x.strip()]
    with db() as conn:
        old=conn.execute('SELECT image FROM questions WHERE id=?',(question_id,)).fetchone()
        if not old:return JSONResponse({'ok':False,'message':'Вопрос не найден.'},status_code=404)
        filename=old['image']
        if remove_image and filename:
            (UPLOAD_DIR/filename).unlink(missing_ok=True);filename=None
        if image and image.filename:
            if image.content_type not in ALLOWED_IMAGE_TYPES:return JSONResponse({'ok':False,'message':'Допустимы JPG, PNG, WEBP или GIF.'},status_code=400)
            if filename:(UPLOAD_DIR/filename).unlink(missing_ok=True)
            filename=f'{uuid.uuid4().hex}{ALLOWED_IMAGE_TYPES[image.content_type]}'
            (UPLOAD_DIR/filename).write_bytes(await image.read())
        conn.execute('''UPDATE questions SET round_id=?,type=?,text=?,image=?,options_json=?,answer_json=?,aliases_json=?,explanation=?,points=?,time_limit=? WHERE id=?''',
                     (round_id,q_type,text.strip(),filename,json.dumps(opts,ensure_ascii=False),json.dumps(ans,ensure_ascii=False),json.dumps(als,ensure_ascii=False),explanation.strip(),max(1,points),time_limit or None,question_id))
    return JSONResponse({'ok':True})

@app.delete('/api/admin/questions/{question_id}')
async def delete_question(request: Request, question_id: int):
    err=check_admin(request)
    if err:return err
    with db() as conn:
        row=conn.execute('SELECT image,round_id,position FROM questions WHERE id=?',(question_id,)).fetchone()
        if not row:return JSONResponse({'ok':False,'message':'Вопрос не найден.'},status_code=404)
        if row['image']:(UPLOAD_DIR/row['image']).unlink(missing_ok=True)
        conn.execute('DELETE FROM questions WHERE id=?',(question_id,))
        conn.execute('UPDATE questions SET position=position-1 WHERE round_id=? AND position>?',(row['round_id'],row['position']))
    return JSONResponse({'ok':True})

@app.post('/api/admin/new')
async def admin_new(request: Request):
    err=check_admin(request)
    if err:return err
    with db() as conn:
        conn.execute('DELETE FROM teams');conn.execute('DELETE FROM answers');conn.execute('DELETE FROM reactions')
        conn.execute('UPDATE state SET room_code=?,phase=?,current_question_id=?,deadline=?,started_at=? WHERE id=1',(make_code(),'waiting',None,0,0))
    return JSONResponse({'ok':True,'state':current_state()})

@app.post('/api/admin/start')
async def admin_start(request: Request):
    err=check_admin(request)
    if err:return err
    s=current_state()
    if s['phase']!='waiting':return JSONResponse({'ok':False,'message':'Квиз уже запущен. Используйте «Начать заново».'},status_code=400)
    qs=all_questions()
    if not qs:return JSONResponse({'ok':False,'message':'Добавьте хотя бы один вопрос.'},status_code=400)
    qid=qs[0]['id']; t=now(); limit=qs[0]['time_limit'] or get_settings()['question_time']
    with db() as conn:
        conn.execute('DELETE FROM answers');conn.execute('UPDATE teams SET score=0');conn.execute('UPDATE state SET phase=?,current_question_id=?,deadline=?,started_at=? WHERE id=1',('question',qid,t+limit,t))
    return JSONResponse({'ok':True})

@app.post('/api/admin/reveal')
async def admin_reveal(request: Request):
    err=check_admin(request)
    if err:return err
    s=current_state()
    if s['phase']!='question':return JSONResponse({'ok':False,'message':'Сейчас нечего открывать.'},status_code=400)
    with db() as conn:conn.execute('UPDATE state SET phase=? WHERE id=1',('reveal',))
    return JSONResponse({'ok':True})

@app.post('/api/admin/next')
async def admin_next(request: Request):
    err=check_admin(request)
    if err:return err
    s=current_state()
    if s['phase']=='question':
        with db() as conn:conn.execute('UPDATE state SET phase=? WHERE id=1',('reveal',))
        return JSONResponse({'ok':True,'phase':'reveal'})
    if s['phase']!='reveal':return JSONResponse({'ok':False,'message':'Сначала запустите вопрос.'},status_code=400)
    qs=all_questions(); ids=[q['id'] for q in qs];
    try: idx=ids.index(s['current_question_id'])
    except ValueError: idx=-1
    if idx<0 or idx+1>=len(ids):
        with db() as conn:conn.execute('UPDATE state SET phase=?,current_question_id=?,deadline=? WHERE id=1',('finished',None,0))
        return JSONResponse({'ok':True,'phase':'finished'})
    nq=qs[idx+1]; t=now(); limit=nq['time_limit'] or get_settings()['question_time']
    with db() as conn:conn.execute('UPDATE state SET phase=?,current_question_id=?,deadline=? WHERE id=1',('question',nq['id'],t+limit))
    return JSONResponse({'ok':True,'phase':'question'})

@app.post('/api/admin/finish')
async def admin_finish(request: Request):
    err=check_admin(request)
    if err:return err
    with db() as conn:conn.execute('UPDATE state SET phase=?,current_question_id=?,deadline=? WHERE id=1',('finished',None,0))
    return JSONResponse({'ok':True})

@app.get('/api/qr')
async def api_qr(url: str):
    qr=qrcode.QRCode(version=None,box_size=8,border=2);qr.add_data(url);qr.make(fit=True)
    im=qr.make_image(fill_color='black',back_color='white');buf=io.BytesIO();im.save(buf,format='PNG');buf.seek(0)
    return StreamingResponse(buf,media_type='image/png',headers={'Cache-Control':'no-store'})

@app.post('/api/join')
async def join(room_code: str=Form(...), team_name: str=Form(...)):
    s=current_state();room_code=room_code.strip();team_name=team_name.strip()
    if room_code!=s['room_code']:return JSONResponse({'ok':False,'message':'Неверный код квиза.'},status_code=400)
    if not team_name:return JSONResponse({'ok':False,'message':'Введите название команды.'},status_code=400)
    if len(team_name)>40:return JSONResponse({'ok':False,'message':'Название слишком длинное.'},status_code=400)
    t=now()
    with db() as conn:
        ex=conn.execute('SELECT team_code FROM teams WHERE team_name=?',(team_name,)).fetchone()
        if ex: code=ex['team_code'];conn.execute('UPDATE teams SET last_seen=? WHERE team_code=?',(t,code))
        else:
            code=secrets.token_hex(4).upper();conn.execute('INSERT INTO teams(team_code,team_name,score,joined_at,last_seen) VALUES(?,?,?,?,?)',(code,team_name,0,t,t))
    return JSONResponse({'ok':True,'team_code':code,'team_name':team_name,'room_code':room_code})

@app.get('/api/team/{team_code}')
async def team_state(team_code: str):
    s=current_state();
    with db() as conn:
        conn.execute('UPDATE teams SET last_seen=? WHERE team_code=?',(now(),team_code))
        team=conn.execute('SELECT * FROM teams WHERE team_code=?',(team_code,)).fetchone()
        if not team:return JSONResponse({'ok':False,'message':'Команда не найдена.'},status_code=404)
        ans=conn.execute('SELECT * FROM answers WHERE team_code=? AND question_id=?',(team_code,s['current_question_id'] or -1)).fetchone()
    q=public_current_question(include_answer=s['phase'] in ('reveal','finished'))
    result=None
    if ans and q and s['phase'] in ('reveal','finished'):
        submitted=json.loads(ans['value_json'])
        if isinstance(submitted,list): submitted_text=', '.join(submitted)
        else: submitted_text=submitted
        result={'submitted':submitted_text,'submitted_list': submitted if isinstance(submitted,list) else [submitted_text], 'correct':bool(ans['correct']),'correct_answer':q['answer'],'explanation':q.get('explanation',''),'points_awarded':ans['points_awarded']}
    return JSONResponse({'ok':True,'state':s,'question':q,'team':dict(team),'answered':bool(ans),'result':result,'leaderboard':leaderboard()})

@app.post('/api/answer')
async def answer(team_code: str=Form(...), question_id: int=Form(...), value: str=Form(...)):
    s=current_state()
    if s['phase']!='question':return JSONResponse({'ok':False,'message':'Приём ответов закрыт.'},status_code=400)
    if question_id!=s['current_question_id'] or now()>=s['deadline']:return JSONResponse({'ok':False,'message':'Время на этот вопрос истекло.'},status_code=400)
    q=current_question_full()
    if not q:return JSONResponse({'ok':False,'message':'Вопрос не найден.'},status_code=400)
    t=now();correct=evaluate(q,value);points=q['points'] if correct else 0
    parsed=parse_answer(q,value)
    val=json.dumps(parsed,ensure_ascii=False)
    with db() as conn:
        team=conn.execute('SELECT team_code FROM teams WHERE team_code=?',(team_code,)).fetchone()
        if not team:return JSONResponse({'ok':False,'message':'Команда не найдена.'},status_code=404)
        if conn.execute('SELECT 1 FROM answers WHERE team_code=? AND question_id=?',(team_code,question_id)).fetchone():return JSONResponse({'ok':False,'message':'Ответ уже принят.'},status_code=400)
        conn.execute('INSERT INTO answers(team_code,question_id,value_json,correct,points_awarded,submitted_at) VALUES(?,?,?,?,?,?)',(team_code,question_id,val,int(correct),points,t))
        conn.execute('UPDATE teams SET score=score+?,last_seen=? WHERE team_code=?',(points,t,team_code))
        score=conn.execute('SELECT score FROM teams WHERE team_code=?',(team_code,)).fetchone()['score']
    return JSONResponse({'ok':True,'correct':correct,'score':score})

@app.post('/api/reaction')
async def reaction(team_code: str=Form(''),emoji: str=Form(...)):
    if emoji not in REACTIONS:return JSONResponse({'ok':False,'message':'Неизвестная реакция.'},status_code=400)
    with db() as conn:conn.execute('INSERT INTO reactions(team_code,emoji,created_at) VALUES(?,?,?)',(team_code or None,emoji,now()))
    return JSONResponse({'ok':True})

@app.get('/api/reactions')
async def reactions(since: int=0):
    with db() as conn:
        rows=conn.execute('SELECT id,emoji,created_at FROM reactions WHERE id>? ORDER BY id ASC LIMIT 100',(since,)).fetchall()
    return JSONResponse({'reactions':[dict(r) for r in rows]})
