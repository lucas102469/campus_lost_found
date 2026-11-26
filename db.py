"""SQLite 연결, 스키마, 반복 실행해도 중복되지 않는 예시 데이터."""
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import current_app, g

BUILDINGS = {'우당관':[f'{i}층' for i in range(1,11)], '충효관':[f'{i}층' for i in range(1,6)], '선덕관':[f'{i}층' for i in range(1,8)], '양현원':['1층'], '청사종합체육관':['지하 1층','1층','2층','3층']}
CATEGORIES = ['전자기기','가방','지갑','의류','기타']
# 기록 시간을 한국 시간으로 생성한다.
def now():
    return datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y.%m.%d %H:%M:%S')
# 요청별 SQLite 연결을 재사용하고 외래 키 검사를 활성화한다.
def get_db():
    if 'db' not in g:
        g.db=sqlite3.connect(current_app.config['DATABASE'],timeout=10)
        g.db.row_factory=sqlite3.Row
        g.db.execute('PRAGMA foreign_keys=ON')
    return g.db

# 요청 처리가 끝나면 해당 DB 연결을 닫는다.
def close_db(error=None):
    db=g.pop('db',None)
    if db: db.close()

SCHEMA='''
CREATE TABLE IF NOT EXISTS students(student_no TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS items(
 id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL REFERENCES students(student_no),
 kind TEXT NOT NULL CHECK(kind IN ('lost','found')), title TEXT NOT NULL, category TEXT NOT NULL,
 appearance TEXT NOT NULL, found_place TEXT NOT NULL DEFAULT '', extra TEXT NOT NULL DEFAULT '',
 photo TEXT, photo_pos TEXT, status TEXT NOT NULL, storage_type TEXT,
 building TEXT, floor TEXT, room TEXT, latitude REAL, longitude REAL, location_note TEXT,
 created_at TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS item_photos(id INTEGER PRIMARY KEY AUTOINCREMENT,item_id INTEGER NOT NULL REFERENCES items(id),filename TEXT NOT NULL,position INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS chats(
 id INTEGER PRIMARY KEY AUTOINCREMENT,item_id INTEGER NOT NULL REFERENCES items(id),
 author TEXT NOT NULL REFERENCES students(student_no), other TEXT NOT NULL REFERENCES students(student_no),
 finder TEXT NOT NULL REFERENCES students(student_no), loser TEXT NOT NULL REFERENCES students(student_no),
 created_at TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
 UNIQUE(item_id,author,other), CHECK(author != other)
);
CREATE TABLE IF NOT EXISTS messages(
 id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id INTEGER NOT NULL REFERENCES chats(id),
 sender TEXT NOT NULL REFERENCES students(student_no),body TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS location_shares(
 chat_id INTEGER PRIMARY KEY REFERENCES chats(id), sender TEXT NOT NULL REFERENCES students(student_no),
 storage_type TEXT NOT NULL,building TEXT,floor TEXT,room TEXT,latitude REAL,longitude REAL,location_note TEXT,
 linked_lost_id INTEGER REFERENCES items(id),shared_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS returns(
 chat_id INTEGER PRIMARY KEY REFERENCES chats(id), finder TEXT NOT NULL,loser TEXT NOT NULL,
 found_item_id INTEGER REFERENCES items(id),lost_item_id INTEGER REFERENCES items(id),completed_at TEXT NOT NULL,
 UNIQUE(found_item_id),UNIQUE(lost_item_id)
);
CREATE TABLE IF NOT EXISTS hidden_chats(student_no TEXT NOT NULL REFERENCES students(student_no),chat_id INTEGER NOT NULL REFERENCES chats(id),PRIMARY KEY(student_no,chat_id));
CREATE TABLE IF NOT EXISTS category_subscriptions(student_no TEXT NOT NULL REFERENCES students(student_no),category TEXT NOT NULL,PRIMARY KEY(student_no,category));
CREATE TABLE IF NOT EXISTS notifications(
 id INTEGER PRIMARY KEY AUTOINCREMENT,recipient TEXT NOT NULL REFERENCES students(student_no),
 event_type TEXT NOT NULL,event_key TEXT NOT NULL,body TEXT NOT NULL,item_id INTEGER REFERENCES items(id),
 chat_id INTEGER REFERENCES chats(id),created_at TEXT NOT NULL,is_read INTEGER NOT NULL DEFAULT 0,
 UNIQUE(recipient,event_type,event_key)
);
CREATE INDEX IF NOT EXISTS item_filters ON items(deleted,kind,category,status);
CREATE INDEX IF NOT EXISTS chat_messages ON messages(chat_id,id);
'''

# 수신자·이벤트 종류·이벤트 키의 UNIQUE 제약과 INSERT OR IGNORE로 같은 알림의 중복 생성을 막는다.
def notify(db,recipient,event_type,event_key,body,item_id=None,chat_id=None):
    db.execute('INSERT OR IGNORE INTO notifications(recipient,event_type,event_key,body,item_id,chat_id,created_at) VALUES(?,?,?,?,?,?,?)', (recipient,event_type,str(event_key),body,item_id,chat_id,now()))

# 물품 유형과 카테고리 구독 조건 및 관련 분실물 조건에 맞는 사용자만 선정한다. 등록자 본인은 제외한다.
def match_notifications(db,item):
    recipients={row[0] for row in db.execute("SELECT student_no FROM category_subscriptions WHERE category=? AND student_no!=? AND instr(','||kinds||',',','||?||',')>0",(item['category'],item['owner'],item['kind']))}
    if item['kind']=='found':
        recipients.update(row[0] for row in db.execute("SELECT DISTINCT owner FROM items WHERE kind='lost' AND status='찾는 중' AND deleted=0 AND category=? AND owner!=?",(item['category'],item['owner'])))
    for recipient in recipients:
        notify(db,recipient,'match',item['id'],'관련 '+('습득물' if item['kind']=='found' else '분실물')+'이 등록되었습니다.',item_id=item['id'])

# 테이블과 기존 DB의 추가 컬럼을 준비한다. 초기 예시 데이터는 중복 생성하지 않는다.
def init_db(seed=True):
    db=get_db();db.executescript(SCHEMA)
    # 기존 DB도 유지하면서 거절 종료 여부를 별도로 기록한다.
    if 'rejected' not in [row['name'] for row in db.execute('PRAGMA table_info(chats)')]:
        db.execute('ALTER TABLE chats ADD COLUMN rejected INTEGER NOT NULL DEFAULT 0')
    if 'kinds' not in [row['name'] for row in db.execute('PRAGMA table_info(category_subscriptions)')]:
        db.execute("ALTER TABLE category_subscriptions ADD COLUMN kinds TEXT NOT NULL DEFAULT 'found'")
    if 'original_name' not in [row['name'] for row in db.execute('PRAGMA table_info(item_photos)')]:
        db.execute('ALTER TABLE item_photos ADD COLUMN original_name TEXT')
    if 'display_name' not in [row['name'] for row in db.execute('PRAGMA table_info(students)')]:
        db.execute('ALTER TABLE students ADD COLUMN display_name TEXT')
    for n in ['2221054','2221055','2221056']: db.execute('INSERT OR IGNORE INTO students(student_no) VALUES(?)',(n,))
    if seed and not db.execute('SELECT 1 FROM items LIMIT 1').fetchone():
        examples=[
          ('2221055','found','지갑','지갑','검정색 소가죽, 브랜드 로고 없음','캠퍼스 보행로','wallet.png',None,'보관 중','internal','우당관','3층','301호',None,None,None),
          ('2221054','lost','휴대폰','전자기기','실버 색상, 투명 젤리 케이스 착용','도서관','samples.png','0% 0%','찾는 중',None,None,None,None,None,None,None),
          ('2221056','found','무선이어폰','전자기기','흰색 무선이어폰 충전 케이스','캠퍼스 휴게공간','samples.png','100% 0%','보관 중','external',None,None,None,37.8074,127.0712,'예시 좌표: 캠퍼스 벤치 옆 — 실제 위치를 확인하고 사용하세요'),
          ('2221055','found','가방','가방','네이비 백팩, 노트북 수납 공간 있음','캠퍼스','samples.png','0% 100%','반환 완료','internal','선덕관','2층','201호',None,None,None),
          ('2221054','lost','지갑','지갑','검정색 가죽 지갑을 찾습니다','우당관 근처','wallet.png',None,'찾는 중',None,None,None,None,None,None,None),
          ('2221056','lost','우산','기타','검정색 접이식 우산','충효관','samples.png','100% 100%','찾는 중',None,None,None,None,None,None,None)]
        for row in examples:
            db.execute('INSERT INTO items(owner,kind,title,category,appearance,found_place,photo,photo_pos,status,storage_type,building,floor,room,latitude,longitude,location_note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(*row,now()))
        # 채팅 1: 습득물 글에 분실자가 연락. 아직 위치는 미공개.
        db.execute('INSERT INTO chats(item_id,author,other,finder,loser,created_at) VALUES(1,?,?,?,?,?)',('2221055','2221054','2221055','2221054',now()))
        # 채팅 2: 분실물 글에 습득자가 연락. 위치 지정하기 시험용.
        db.execute('INSERT INTO chats(item_id,author,other,finder,loser,created_at) VALUES(6,?,?,?,?,?)',('2221056','2221055','2221055','2221056',now()))
        for cid,sender,body in [(1,'2221054','안녕하세요. 제 지갑인 것 같아요. 안에 교통카드가 있고 안쪽에 작은 별 스티커가 붙어 있어요.'),(1,'2221055','안녕하세요. 말씀하신 특징을 확인해볼게요.'),(2,'2221055','검정 우산을 주웠어요. 손잡이의 특징을 알려주실 수 있나요?'),(2,'2221056','손잡이 아래에 노란색 작은 스티커가 있어요.')]:
            mid=db.execute('INSERT INTO messages(chat_id,sender,body,created_at) VALUES(?,?,?,?)',(cid,sender,body,now())).lastrowid
            target='2221055' if sender in ['2221054','2221056'] else ('2221054' if cid==1 else '2221056')
            notify(db,target,'message',mid,'새로운 메시지가 도착했습니다.',chat_id=cid)
        for item in db.execute('SELECT * FROM items WHERE kind="found" AND status="보관 중"').fetchall():match_notifications(db,item)
    db.commit()
