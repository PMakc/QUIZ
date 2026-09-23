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
REACTIONS = ['👍', '😊', '❤️', '🔥']
QUESTION_TYPES = {'choice', 'multiple', 'bool', 'text'}
ALLOWED_IMAGES = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/gif': '.gif'}


def db():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c


def now():
    return time.time()


def make_code():
    return str(secrets.randbelow(900000) + 100000)


def norm(v: Any) -> str:
    return ' '.join(str(v or '').strip().lower().replace('ё', 'е').split())


def admin_required(request: Request):
    if request.cookies.get('admin_ok') != '1':
        return JSONResponse({'ok': False, 'message': 'Требуется вход в админ-панель.'}, status_code=401)
    return None


def seed_questions():
    return [
        (1, 1, 'bool', 'Обогащение — это отделение полезного компонента от пустой породы.', ['ПРАВДА', 'МИФ'], ['ПРАВДА'], [], 'Если представить руду как смесь ценного минерала и обычной породы, наша задача — отделить полезное от ненужного.'),
        (1, 2, 'bool', 'Флотация основана на магнитных свойствах.', ['ПРАВДА', 'МИФ'], ['МИФ'], [], 'Флотация связана с пузырьками воздуха и специальными реагентами. Разделение по магнитным свойствам — магнитная сепарация.'),
        (1, 3, 'bool', 'Концентрат — это отходы.', ['ПРАВДА', 'МИФ'], ['МИФ'], [], 'Концентрат — продукт с повышенным содержанием полезного компонента. Отходы называют хвостами.'),
        (1, 4, 'bool', 'Вода — это H₂O.', ['ПРАВДА', 'МИФ'], ['ПРАВДА'], [], 'Два атома водорода и один атом кислорода.'),
        (1, 5, 'bool', 'Кислород — это металл.', ['ПРАВДА', 'МИФ'], ['МИФ'], [], 'Кислород — неметалл и при обычных условиях находится в газообразном состоянии.'),
        (1, 6, 'bool', 'Поваренная соль — это NaCl.', ['ПРАВДА', 'МИФ'], ['ПРАВДА'], [], 'NaCl — хлорид натрия.'),
        (1, 7, 'bool', 'Настоящее золото мягкое, а пирит твёрдый.', ['ПРАВДА', 'МИФ'], ['ПРАВДА'], [], 'Золото относительно мягкое, а пирит значительно твёрже.'),
        (2, 1, 'choice', 'Как называется метод обогащения, где используют магнит?', ['Флотация', 'Магнитная сепарация', 'Грохочение', 'Промывка'], ['Магнитная сепарация'], [], 'Метод разделяет минералы, обладающие магнитными свойствами, и немагнитную часть материала.'),
        (2, 2, 'choice', 'Как называется метод обогащения, при котором частицы руды разделяются с помощью пузырьков воздуха?', ['Магнитная сепарация', 'Гравитация', 'Флотация', 'Дробление'], ['Флотация'], [], 'В пульпу добавляют реагенты, воздух образует пузырьки, а определённые минералы закрепляются на них и поднимаются с пеной.'),
        (2, 3, 'choice', 'Какой метод обогащения основан на разнице плотности?', ['Флотация', 'Гравитационное обогащение', 'Магнитная сепарация', 'Измельчение'], ['Гравитационное обогащение'], [], 'Тяжёлые частицы оседают, а лёгкие уносятся водой; пример — промывка золота.'),
        (2, 4, 'choice', 'Как называется процесс разделения материала по крупности с помощью сит?', ['Дробление', 'Флотация', 'Грохочение', 'Промывка'], ['Грохочение'], [], 'Крупные куски остаются сверху, а мелкие проходят через отверстия сит.'),
        (2, 5, 'choice', 'Как называется процесс уменьшения размеров кусков руды?', ['Грохочение', 'Флотация', 'Дробление и измельчение', 'Магнитная сепарация'], ['Дробление и измельчение'], [], 'Сначала крупные куски руды уменьшают в размерах, чтобы раскрыть минеральные зёрна и затем их разделить.'),
        (3, 1, 'text', 'Какой полезный металл добывают из минерала магнетита?', [], ['ЖЕЛЕЗО'], ['железо'], 'Магнетит — важный железорудный минерал.'),
        (3, 2, 'text', 'За схожесть с золотом этот минерал прозвали «золотом дураков». Что это?', [], ['ПИРИТ'], ['пирит'], 'Пирит может напоминать золото по цвету и блеску.'),
        (3, 3, 'text', 'Самая твёрдая природная форма углерода. Используется в ювелирной промышленности и для обработки очень твёрдых материалов.', [], ['АЛМАЗ'], ['алмаз'], 'Алмаз имеет твёрдость 10 по шкале Мооса; алмаз и графит состоят из углерода, но имеют разное строение кристаллической решётки.'),
        (3, 4, 'text', 'Как называется простой инструмент старателей для поиска и промывки золота?', [], ['ЛОТОК'], ['лоток', 'золотопромывочный лоток'], 'Золото плотнее обычного песка, поэтому при промывке оно остаётся в лотке.'),
        (3, 5, 'text', 'В сердце Забайкалья есть город, известный как крупный центр добычи стратегического сырья. Что там добывают?', [], ['УРАН'], ['уран'], 'В исходном сценарии правильный ответ указан как «уран».'),
    ]


def init_db():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1), title TEXT NOT NULL, subtitle TEXT NOT NULL, question_time INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS rounds(id INTEGER PRIMARY KEY AUTOINCREMENT, position INTEGER NOT NULL, title TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS questions(
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
        CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY CHECK(id=1), room_code TEXT NOT NULL, phase TEXT NOT NULL, current_question_id INTEGER, deadline REAL NOT NULL DEFAULT 0, started_at REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS teams(team_code TEXT PRIMARY KEY, team_name TEXT NOT NULL UNIQUE, score INTEGER NOT NULL DEFAULT 0, joined_at REAL NOT NULL, last_seen REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS answers(team_code TEXT NOT NULL REFERENCES teams(team_code) ON DELETE CASCADE, question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE, value_json TEXT NOT NULL, correct INTEGER NOT NULL, points_awarded INTEGER NOT NULL DEFAULT 0, submitted_at REAL NOT NULL, PRIMARY KEY(team_code,question_id));
        CREATE TABLE IF NOT EXISTS reactions(id INTEGER PRIMARY KEY AUTOINCREMENT, team_code TEXT, emoji TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_q_round_pos ON questions(round_id,position);
        CREATE INDEX IF NOT EXISTS idx_answers_q ON answers(question_id);
        CREATE INDEX IF NOT EXISTS idx_reactions_id ON reactions(id);
        ''')
        if not c.execute('SELECT 1 FROM settings WHERE id=1').fetchone():
            c.execute('INSERT INTO settings VALUES(1,?,?,?)', ('Алхимик', 'Синдром Эфирита', DEFAULT_QUESTION_TIME))
        if not c.execute('SELECT 1 FROM rounds LIMIT 1').fetchone():
            for pos,title in [(1,'ВЕРЮ — НЕ ВЕРЮ'),(2,'УГАДАЙ МЕТОД'),(3,'ВПИШИ СЛОВО')]: c.execute('INSERT INTO rounds(position,title) VALUES(?,?)',(pos,title))
        if not c.execute('SELECT 1 FROM questions LIMIT 1').fetchone():
            rmap={r['position']:r['id'] for r in c.execute('SELECT id,position FROM rounds')}
            for rd,pos,typ,text,opts,ans,aliases,expl in seed_questions():
                c.execute('INSERT INTO questions(round_id,position,type,text,image,options_json,answer_json,aliases_json,explanation,points,time_limit) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                          (rmap[rd],pos,typ,text,None,json.dumps(opts,ensure_ascii=False),json.dumps(ans,ensure_ascii=False),json.dumps(aliases,ensure_ascii=False),expl,1,None))
        if not c.execute('SELECT 1 FROM state WHERE id=1').fetchone():
            c.execute('INSERT INTO state VALUES(1,?,?,?,?,?)',(make_code(),'waiting',None,0,0))
        # Repair malformed single answers from earlier editor versions like ["\"ПРАВДА\""]
        rows=c.execute("SELECT id,type,answer_json FROM questions WHERE type<>'multiple'").fetchall()
        for r in rows:
            try: arr=json.loads(r['answer_json'] or '[]')
            except: continue
            if isinstance(arr,list) and arr:
                a=arr[0]
                if isinstance(a,str) and len(a)>=2 and a[0]=='"' and a[-1]=='"':
                    try:a=json.loads(a)
                    except:pass
                    c.execute('UPDATE questions SET answer_json=? WHERE id=?',(json.dumps([a],ensure_ascii=False),r['id']))
        c.commit()


def settings():
    with db() as c:return dict(c.execute('SELECT * FROM settings WHERE id=1').fetchone())


def question_dict(row, public=False):
    d=dict(row)
    d['options']=json.loads(d.pop('options_json') or '[]')
    answers=json.loads(d.pop('answer_json') or '[]')
    d['answer']=answers if d['type']=='multiple' else (answers[0] if answers else '')
    d['aliases']=json.loads(d.pop('aliases_json') or '[]'); d['aliases']=d['aliases'] if isinstance(d['aliases'], list) else ([d['aliases']] if d['aliases'] else [])
    if public:
        d.pop('answer',None);d.pop('aliases',None);d.pop('explanation',None)
    return d


def all_questions():
    with db() as c:
        rows=c.execute('SELECT q.*,r.position round_position,r.title round_title FROM questions q JOIN rounds r ON r.id=q.round_id ORDER BY r.position,q.position,q.id').fetchall()
    return [question_dict(r) for r in rows]


def current_state():
    with db() as c:
        s=dict(c.execute('SELECT * FROM state WHERE id=1').fetchone())
        if s['phase']=='question' and s['deadline'] and now()>=s['deadline']:
            c.execute("UPDATE state SET phase='reveal' WHERE id=1");s['phase']='reveal'
        return s


def current_question():
    s=current_state()
    if not s['current_question_id']:return None
    with db() as c:
        row=c.execute('SELECT q.*,r.position round_position,r.title round_title FROM questions q JOIN rounds r ON r.id=q.round_id WHERE q.id=?',(s['current_question_id'],)).fetchone()
    return question_dict(row) if row else None


def leaderboard():
    with db() as c:return [dict(r) for r in c.execute('SELECT team_code,team_name,score,joined_at,last_seen FROM teams ORDER BY score DESC,team_name COLLATE NOCASE').fetchall()]


def participants():
    t=now();out=[]
    for i,r in enumerate(leaderboard(),1):r['rank']=i;r['online']=t-r['last_seen']<8;out.append(r)
    return out


def parse_value(q,v):
    if q['type']=='multiple':
        try:
            a=json.loads(v);return a if isinstance(a,list) else [str(a)]
        except:return [x.strip() for x in v.split(',') if x.strip()]
    return v.strip()


def is_correct(q,v):
    got=parse_value(q,v)
    if q['type']=='multiple':return {norm(x) for x in got}=={norm(x) for x in q['answer']}
    if q['type']=='text':return norm(got)==norm(q['answer']) or norm(got) in {norm(x) for x in q.get('aliases',[])}
    return norm(got)==norm(q['answer'])


def question_stats(qid):
    with db() as c:
        qrow=c.execute('SELECT type,options_json,answer_json FROM questions WHERE id=?',(qid,)).fetchone()
        if not qrow:return {'total':0,'options':[]}
        typ=qrow['type'];opts=json.loads(qrow['options_json'] or '[]')
        rows=c.execute('SELECT value_json FROM answers WHERE question_id=?',(qid,)).fetchall()
    counts={o:0 for o in opts}; total=len(rows)
    for r in rows:
        try:v=json.loads(r['value_json'])
        except:v=''
        vals=v if isinstance(v,list) else [v]
        for x in vals:
            if x in counts:counts[x]+=1
    out=[]
    for o in opts:
        out.append({'option':o,'count':counts[o],'percent':round((counts[o]/total*100) if total else 0,1)})
    return {'total':total,'options':out}


def team_result(team_code,q):
    s=current_state()
    with db() as c:
        a=c.execute('SELECT * FROM answers WHERE team_code=? AND question_id=?',(team_code,q['id'])).fetchone()
    if not a:return None
    try:sub=json.loads(a['value_json'])
    except:sub=''
    sublist=sub if isinstance(sub,list) else [sub]
    return {'submitted':', '.join(str(x) for x in sublist),'submitted_list':sublist,'correct':bool(a['correct']),'correct_answer':q['answer'],'explanation':q.get('explanation',''),'points_awarded':a['points_awarded'],'revealed':s['phase'] in ('reveal','finished')}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db();yield

app=FastAPI(title='Алхимик — Bothost v8',lifespan=lifespan)
app.mount('/static',StaticFiles(directory=BASE_DIR/'static'),name='static')
app.mount('/media',StaticFiles(directory=UPLOAD_DIR),name='media')
templates=Jinja2Templates(directory=BASE_DIR/'templates')

@app.get('/health')
async def health():return JSONResponse({'ok':True,'version':'bothost-v9'})
@app.get('/favicon.ico')
async def favicon():return JSONResponse({},status_code=204)
@app.get('/',response_class=HTMLResponse)
async def home(request:Request):return templates.TemplateResponse(request=request,name='index.html',context={'settings':settings()})
@app.get('/admin',response_class=HTMLResponse)
async def admin(request:Request):
    if request.cookies.get('admin_ok')!='1':return templates.TemplateResponse(request=request,name='admin_login.html',context={})
    return templates.TemplateResponse(request=request,name='admin.html',context={'settings':settings()})
@app.get('/screen',response_class=HTMLResponse)
async def screen(request:Request):return templates.TemplateResponse(request=request,name='screen.html',context={'settings':settings()})
@app.get('/play',response_class=HTMLResponse)
async def play(request:Request):return templates.TemplateResponse(request=request,name='play.html',context={'settings':settings()})
@app.post('/admin/login')
async def login(password:str=Form(...)):
    if password!=ADMIN_PASSWORD:return HTMLResponse('Неверный пароль',status_code=401)
    r=RedirectResponse('/admin',status_code=303);r.set_cookie('admin_ok','1',httponly=True,samesite='lax');return r

@app.get('/api/state')
async def api_state():
    s=current_state();q=current_question(); pub=None;stats={'total':0,'options':[]}
    if q:
        pub=dict(q);pub['answer']=q['answer'] if s['phase'] in ('reveal','finished') else None
        if s['phase']=='question': pub.pop('aliases',None);pub.pop('explanation',None)
        stats=question_stats(q['id'])
    return JSONResponse({'state':s,'question':pub,'settings':settings(),'leaderboard':leaderboard(),'reaction_counts':reaction_counts(),'stats':stats})

@app.get('/api/admin/config')
async def admin_config(request:Request):
    err=admin_required(request)
    if err:return err
    with db() as c:
        rs=[]
        for r in c.execute('SELECT * FROM rounds ORDER BY position,id').fetchall():
            rr=dict(r);qs=c.execute('SELECT * FROM questions WHERE round_id=? ORDER BY position,id',(r['id'],)).fetchall();rr['questions']=[question_dict(q) for q in qs];rr['question_count']=len(qs);rs.append(rr)
    return JSONResponse({'settings':settings(),'rounds':rs})

@app.get('/api/participants')
async def api_participants(request:Request):
    err=admin_required(request)
    if err:return err
    return JSONResponse({'participants':participants()})

@app.post('/api/admin/settings')
async def save_settings(request:Request,title:str=Form(...),subtitle:str=Form(''),question_time:int=Form(45)):
    err=admin_required(request)
    if err:return err
    with db() as c:c.execute('UPDATE settings SET title=?,subtitle=?,question_time=? WHERE id=1',(title.strip() or 'Квиз',subtitle.strip(),max(5,min(300,question_time))))
    return JSONResponse({'ok':True})

@app.post('/api/admin/rounds')
async def add_round(request:Request,title:str=Form(...)):
    err=admin_required(request)
    if err:return err
    with db() as c:
        pos=c.execute('SELECT COALESCE(MAX(position),0)+1 FROM rounds').fetchone()[0];rid=c.execute('INSERT INTO rounds(position,title) VALUES(?,?)',(pos,title.strip() or 'Новый раунд')).lastrowid
    return JSONResponse({'ok':True,'id':rid})

@app.post('/api/admin/rounds/{rid}')
async def edit_round(request:Request,rid:int,title:str=Form(...)):
    err=admin_required(request)
    if err:return err
    with db() as c:c.execute('UPDATE rounds SET title=? WHERE id=?',(title.strip() or 'Раунд',rid))
    return JSONResponse({'ok':True})

@app.delete('/api/admin/rounds/{rid}')
async def delete_round(request:Request,rid:int):
    err=admin_required(request)
    if err:return err
    with db() as c:
        if c.execute('SELECT COUNT(*) FROM rounds').fetchone()[0]<=1:return JSONResponse({'ok':False,'message':'Нужен хотя бы один раунд.'},status_code=400)
        imgs=c.execute('SELECT image FROM questions WHERE round_id=? AND image IS NOT NULL',(rid,)).fetchall()
        for x in imgs: (UPLOAD_DIR/x['image']).unlink(missing_ok=True)
        c.execute('DELETE FROM rounds WHERE id=?',(rid,))
    return JSONResponse({'ok':True})


def parse_options(text):
    try:v=json.loads(text);return v if isinstance(v,list) else []
    except:return [x.strip() for x in text.split('\n') if x.strip()]

@app.post('/api/admin/questions')
async def add_question(request:Request,round_id:int=Form(...),q_type:str=Form(...),text:str=Form(...),options:str=Form('[]'),answer:str=Form(''),aliases:str=Form('[]'),explanation:str=Form(''),points:int=Form(1),time_limit:int=Form(0),image:UploadFile|None=File(None)):
    err=admin_required(request)
    if err:return err
    if q_type not in QUESTION_TYPES:return JSONResponse({'ok':False,'message':'Неизвестный тип вопроса.'},status_code=400)
    opts=parse_options(options)
    try: parsed=json.loads(answer)
    except: parsed=[]
    if q_type=='multiple':ans=parsed if isinstance(parsed,list) else ([str(parsed)] if parsed else [])
    else:ans=[str(parsed)] if isinstance(parsed,str) else ([str(parsed[0])] if isinstance(parsed,list) and parsed else [''])
    try:als=json.loads(aliases);als=als if isinstance(als,list) else []
    except:als=[]
    filename=None
    if image and image.filename:
        if image.content_type not in ALLOWED_IMAGES:return JSONResponse({'ok':False,'message':'Для фото используйте JPG, PNG, WEBP или GIF.'},status_code=400)
        filename=f'{uuid.uuid4().hex}{ALLOWED_IMAGES[image.content_type]}';(UPLOAD_DIR/filename).write_bytes(await image.read())
    with db() as c:
        pos=c.execute('SELECT COALESCE(MAX(position),0)+1 FROM questions WHERE round_id=?',(round_id,)).fetchone()[0]
        qid=c.execute('INSERT INTO questions(round_id,position,type,text,image,options_json,answer_json,aliases_json,explanation,points,time_limit) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(round_id,pos,q_type,text.strip(),filename,json.dumps(opts,ensure_ascii=False),json.dumps(ans,ensure_ascii=False),json.dumps(als,ensure_ascii=False),explanation.strip(),max(1,points),time_limit or None)).lastrowid
    return JSONResponse({'ok':True,'id':qid})

@app.post('/api/admin/questions/{qid}')
async def edit_question(request:Request,qid:int,round_id:int=Form(...),q_type:str=Form(...),text:str=Form(...),options:str=Form('[]'),answer:str=Form(''),aliases:str=Form('[]'),explanation:str=Form(''),points:int=Form(1),time_limit:int=Form(0),image:UploadFile|None=File(None),remove_image:bool=Form(False)):
    err=admin_required(request)
    if err:return err
    opts=parse_options(options)
    try:parsed=json.loads(answer)
    except:parsed=[]
    if q_type=='multiple':ans=parsed if isinstance(parsed,list) else ([str(parsed)] if parsed else [])
    else:ans=[str(parsed)] if isinstance(parsed,str) else ([str(parsed[0])] if isinstance(parsed,list) and parsed else [''])
    try:als=json.loads(aliases);als=als if isinstance(als,list) else []
    except:als=[]
    with db() as c:
        old=c.execute('SELECT * FROM questions WHERE id=?',(qid,)).fetchone()
        if not old:return JSONResponse({'ok':False,'message':'Вопрос не найден.'},status_code=404)
        filename=old['image']
        if remove_image and filename:(UPLOAD_DIR/filename).unlink(missing_ok=True);filename=None
        if image and image.filename:
            if image.content_type not in ALLOWED_IMAGES:return JSONResponse({'ok':False,'message':'Для фото используйте JPG, PNG, WEBP или GIF.'},status_code=400)
            if filename:(UPLOAD_DIR/filename).unlink(missing_ok=True)
            filename=f'{uuid.uuid4().hex}{ALLOWED_IMAGES[image.content_type]}';(UPLOAD_DIR/filename).write_bytes(await image.read())
        c.execute('UPDATE questions SET round_id=?,type=?,text=?,image=?,options_json=?,answer_json=?,aliases_json=?,explanation=?,points=?,time_limit=? WHERE id=?',(round_id,q_type,text.strip(),filename,json.dumps(opts,ensure_ascii=False),json.dumps(ans,ensure_ascii=False),json.dumps(als,ensure_ascii=False),explanation.strip(),max(1,points),time_limit or None,qid))
    return JSONResponse({'ok':True})

@app.delete('/api/admin/questions/{qid}')
async def delete_question(request:Request,qid:int):
    err=admin_required(request)
    if err:return err
    with db() as c:
        q=c.execute('SELECT image,round_id,position FROM questions WHERE id=?',(qid,)).fetchone()
        if not q:return JSONResponse({'ok':False,'message':'Вопрос не найден.'},status_code=404)
        if q['image']:(UPLOAD_DIR/q['image']).unlink(missing_ok=True)
        c.execute('DELETE FROM questions WHERE id=?',(qid,));c.execute('UPDATE questions SET position=position-1 WHERE round_id=? AND position>?',(q['round_id'],q['position']))
    return JSONResponse({'ok':True})

@app.post('/api/admin/new')
async def new_quiz(request:Request):
    err=admin_required(request)
    if err:return err
    with db() as c:
        c.execute('DELETE FROM teams');c.execute('DELETE FROM answers');c.execute('DELETE FROM reactions');c.execute('UPDATE state SET room_code=?,phase=?,current_question_id=?,deadline=?,started_at=? WHERE id=1',(make_code(),'waiting',None,0,0))
    return JSONResponse({'ok':True,'state':current_state()})

@app.post('/api/admin/start')
async def start_quiz(request:Request):
    err=admin_required(request)
    if err:return err
    s=current_state();qs=all_questions()
    if s['phase']!='waiting':return JSONResponse({'ok':False,'message':'Квиз уже запущен. Сначала нажмите «Начать заново».'},status_code=400)
    if not qs:return JSONResponse({'ok':False,'message':'Добавьте хотя бы один вопрос.'},status_code=400)
    q=qs[0];limit=q['time_limit'] or settings()['question_time'];t=now()
    with db() as c:c.execute('DELETE FROM answers');c.execute('UPDATE teams SET score=0');c.execute('UPDATE state SET phase=?,current_question_id=?,deadline=?,started_at=? WHERE id=1',('question',q['id'],t+limit,t))
    return JSONResponse({'ok':True})

@app.post('/api/admin/reveal')
async def reveal(request:Request):
    err=admin_required(request)
    if err:return err
    if current_state()['phase']!='question':return JSONResponse({'ok':False,'message':'Сейчас нечего открывать.'},status_code=400)
    with db() as c:c.execute("UPDATE state SET phase='reveal' WHERE id=1")
    return JSONResponse({'ok':True})

@app.post('/api/admin/next')
async def next_question(request:Request):
    err=admin_required(request)
    if err:return err
    s=current_state()
    if s['phase']=='question':
        with db() as c:c.execute("UPDATE state SET phase='reveal' WHERE id=1")
        return JSONResponse({'ok':True,'phase':'reveal'})
    if s['phase']!='reveal':return JSONResponse({'ok':False,'message':'Сейчас нельзя перейти дальше.'},status_code=400)
    qs=all_questions();ids=[x['id'] for x in qs]
    try:i=ids.index(s['current_question_id'])
    except ValueError:i=-1
    if i<0 or i+1>=len(ids):
        with db() as c:c.execute("UPDATE state SET phase='finished',current_question_id=NULL,deadline=0 WHERE id=1")
        return JSONResponse({'ok':True,'phase':'finished'})
    q=qs[i+1];t=now();limit=q['time_limit'] or settings()['question_time']
    with db() as c:c.execute('UPDATE state SET phase=?,current_question_id=?,deadline=? WHERE id=1',('question',q['id'],t+limit))
    return JSONResponse({'ok':True})

@app.post('/api/admin/finish')
async def finish(request:Request):
    err=admin_required(request)
    if err:return err
    with db() as c:c.execute("UPDATE state SET phase='finished',current_question_id=NULL,deadline=0 WHERE id=1")
    return JSONResponse({'ok':True})

@app.get('/api/qr')
async def qr(url:str):
    q=qrcode.QRCode(version=None,box_size=8,border=2);q.add_data(url);q.make(fit=True);im=q.make_image(fill_color='black',back_color='white');b=io.BytesIO();im.save(b,'PNG');b.seek(0);return StreamingResponse(b,media_type='image/png')

@app.post('/api/join')
async def join(room_code:str=Form(...),team_name:str=Form(...)):
    s=current_state();room_code=room_code.strip();team_name=team_name.strip()
    if room_code!=s['room_code']:return JSONResponse({'ok':False,'message':'Неверный код квиза.'},status_code=400)
    if not team_name:return JSONResponse({'ok':False,'message':'Введите название команды.'},status_code=400)
    with db() as c:
        ex=c.execute('SELECT team_code FROM teams WHERE team_name=?',(team_name,)).fetchone();t=now()
        if ex:code=ex['team_code'];c.execute('UPDATE teams SET last_seen=? WHERE team_code=?',(t,code))
        else:code=secrets.token_hex(4).upper();c.execute('INSERT INTO teams(team_code,team_name,score,joined_at,last_seen) VALUES(?,?,?,?,?)',(code,team_name,0,t,t))
    return JSONResponse({'ok':True,'team_code':code,'team_name':team_name,'room_code':room_code})

@app.get('/api/team/{team_code}')
async def team(team_code:str):
    s=current_state()
    with db() as c:
        tm=c.execute('SELECT * FROM teams WHERE team_code=?',(team_code,)).fetchone()
        if not tm:return JSONResponse({'ok':False,'message':'Команда не найдена.','code':'TEAM_NOT_FOUND'},status_code=404)
        c.execute('UPDATE teams SET last_seen=? WHERE team_code=?',(now(),team_code))
    q=current_question();result=team_result(team_code,q) if q else None;stats=question_stats(q['id']) if q else {'total':0,'options':[]}
    pub=dict(q) if q else None
    if pub and s['phase']=='question':pub['answer']=None;pub.pop('aliases',None);pub.pop('explanation',None)
    return JSONResponse({'ok':True,'state':s,'question':pub,'team':dict(tm),'answered':result is not None,'result':result,'stats':stats,'leaderboard':leaderboard(),'reaction_counts':reaction_counts()})

@app.post('/api/answer')
async def answer(team_code:str=Form(...),question_id:int=Form(...),value:str=Form(...)):
    s=current_state()
    if s['phase']!='question':return JSONResponse({'ok':False,'message':'Приём ответов закрыт.'},status_code=400)
    if question_id!=s['current_question_id']:return JSONResponse({'ok':False,'message':'Это уже не текущий вопрос.'},status_code=409)
    if now()>=s['deadline']:return JSONResponse({'ok':False,'message':'Время на вопрос истекло.'},status_code=409)
    q=current_question()
    if not q:return JSONResponse({'ok':False,'message':'Вопрос не найден.'},status_code=404)
    with db() as c:
        if not c.execute('SELECT 1 FROM teams WHERE team_code=?',(team_code,)).fetchone():return JSONResponse({'ok':False,'message':'Команда не найдена.','code':'TEAM_NOT_FOUND'},status_code=404)
        if c.execute('SELECT 1 FROM answers WHERE team_code=? AND question_id=?',(team_code,question_id)).fetchone():return JSONResponse({'ok':False,'message':'Ответ уже принят.','code':'ANSWER_EXISTS'},status_code=409)
        correct=is_correct(q,value);pts=q['points'] if correct else 0;parsed=parse_value(q,value);t=now()
        c.execute('INSERT INTO answers(team_code,question_id,value_json,correct,points_awarded,submitted_at) VALUES(?,?,?,?,?,?)',(team_code,question_id,json.dumps(parsed,ensure_ascii=False),int(correct),pts,t));c.execute('UPDATE teams SET score=score+?,last_seen=? WHERE team_code=?',(pts,t,team_code));score=c.execute('SELECT score FROM teams WHERE team_code=?',(team_code,)).fetchone()['score']
    return JSONResponse({'ok':True,'correct':correct,'score':score,'stats':question_stats(question_id),'reaction_counts':reaction_counts()})


def reaction_counts():
    with db() as c:rows=c.execute('SELECT emoji,COUNT(*) c FROM reactions GROUP BY emoji').fetchall()
    out={x:0 for x in REACTIONS}
    for r in rows:out[r['emoji']]=int(r['c'])
    return out

@app.post('/api/reaction')
async def reaction(team_code:str=Form(''),emoji:str=Form(...)):
    if emoji not in REACTIONS:return JSONResponse({'ok':False,'message':'Неизвестная реакция.'},status_code=400)
    with db() as c:
        if team_code and not c.execute('SELECT 1 FROM teams WHERE team_code=?',(team_code,)).fetchone():return JSONResponse({'ok':False,'message':'Команда не найдена.','code':'TEAM_NOT_FOUND'},status_code=404)
        c.execute('INSERT INTO reactions(team_code,emoji,created_at) VALUES(?,?,?)',(team_code or None,emoji,now()))
    return JSONResponse({'ok':True,'count':reaction_counts()[emoji]})

@app.get('/api/reactions')
async def reactions(since:int=0):
    with db() as c:rows=c.execute('SELECT id,emoji,created_at FROM reactions WHERE id>? ORDER BY id ASC LIMIT 200',(since,)).fetchall()
    return JSONResponse({'reactions':[dict(r) for r in rows],'counts':reaction_counts()})
