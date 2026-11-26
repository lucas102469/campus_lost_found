"""캠퍼스 분실물: 세션, 등록, 당사자 채팅, 위치 공개, 알림, 지도 API."""
import os, io, math, secrets, uuid
import truststore
# Windows 등 실행 PC가 신뢰하는 인증서로 HTTPS 연결을 검증한다.
truststore.inject_into_ssl()
import requests
from pathlib import Path
from functools import wraps
from urllib.parse import quote
from PIL import Image, UnidentifiedImageError
from dotenv import load_dotenv
from flask import Flask, render_template, request, session, redirect, url_for, flash, abort, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from db import get_db, close_db, init_db, now, notify, match_notifications, BUILDINGS, CATEGORIES
ROOT=Path(__file__).resolve().parent
load_dotenv(ROOT/'.env')

# 로그인 세션이 없는 요청은 로그인 화면으로 보내고, API 요청에는 401을 반환한다.
def login_required(fn):
    @wraps(fn)
    def wrapped(*a,**kw):
        if not session.get('student_no'):
            if request.path.startswith('/api/'):return jsonify(error='로그인이 필요합니다.'),401
            return redirect(url_for('login'))
        return fn(*a,**kw)
    return wrapped

# 앱 설정과 DB 초기화, 화면 및 API 경로를 구성한다. 테스트에서는 설정을 바꿔 사용할 수 있다.
def create_app(test_config=None):
    app=Flask(__name__)
    instance=ROOT/'instance';instance.mkdir(exist_ok=True)
    secret=os.environ.get('SECRET_KEY')
    if not secret:
        f=instance/'session.key'
        if not f.exists(): f.write_text(secrets.token_hex(32));f.chmod(0o600)
        secret=f.read_text().strip()
    app.config.update(SECRET_KEY=secret,DATABASE=str(instance/'campus.sqlite3'),UPLOAD_FOLDER=str(ROOT/'uploads'),
        MAX_CONTENT_LENGTH=6*1024*1024,MAX_FORM_PARTS=None,MAX_FILE_SIZE=5*1024*1024,
        SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE')=='1',
        MAP_TILE_URL=os.getenv('MAP_TILE_URL','https://tile.openstreetmap.org/{z}/{x}/{y}.png'))
    if test_config:app.config.update(test_config)
    Path(app.config['UPLOAD_FOLDER']).mkdir(parents=True,exist_ok=True)
    app.teardown_appcontext(close_db)
    with app.app_context():init_db(seed=app.config.get('SEED_DEMO',True))

    # 다른 사이트에서 임의로 폼 요청을 보내지 못하도록 세션별 CSRF 토큰을 만든다.
    def csrf():
        if 'csrf_token' not in session:session['csrf_token']=secrets.token_hex(24)
        return session['csrf_token']
    @app.before_request
    # 데이터를 변경하는 요청의 CSRF 토큰을 서버에서 검사한다.
    def csrf_check():
        if request.method=='POST':
            supplied=request.headers.get('X-CSRF-Token') or request.form.get('csrf_token','')
            if not session.get('csrf_token') or not secrets.compare_digest(supplied,session['csrf_token']):abort(400,description='요청을 확인할 수 없습니다. 새로고침 후 다시 시도해주세요.')
    @app.after_request
    # 브라우저 응답에 보안 및 캐시 관련 헤더를 설정한다.
    def response_headers(r):
        r.headers['X-Content-Type-Options']='nosniff';r.headers['X-Frame-Options']='DENY';r.headers['Referrer-Policy']='strict-origin-when-cross-origin'
        if not request.path.startswith('/static/'):r.headers['Cache-Control']='no-store'
        return r
    @app.context_processor
    # 모든 템플릿에서 사용할 로그인 사용자와 읽지 않은 알림 수를 전달한다.
    def common():
        me=session.get('student_no');db=get_db()
        unread=db.execute('SELECT COUNT(*) FROM notifications WHERE recipient=? AND is_read=0',(me,)).fetchone()[0] if me else 0
        return dict(me=me,csrf_token=csrf(),unread=unread,buildings=BUILDINGS,categories=CATEGORIES,map_tile_url=app.config['MAP_TILE_URL'])
    # 삭제되지 않은 물품을 조회하고 존재하지 않으면 접근을 중단한다.
    def item_get(i):
        item=get_db().execute('SELECT * FROM items WHERE id=? AND deleted=0',(i,)).fetchone()
        if not item:abort(404)
        return item
    # 등록자 본인인지 검사하여 다른 사용자의 물품 수정과 삭제를 막는다.
    def owner(item):
        if item['owner']!=session['student_no']:abort(403)
    # 채팅방을 조회하고 현재 사용자가 대화 당사자인지 검사한다.
    def chat_get(c):
        chat=get_db().execute('SELECT c.*,i.kind,i.title,i.owner,i.deleted,i.status,i.photo,i.photo_pos FROM chats c JOIN items i ON i.id=c.item_id WHERE c.id=?',(c,)).fetchone()
        if not chat:abort(404)
        if session['student_no'] not in [chat['author'],chat['other']]:abort(403)
        # 기존 DB의 다른 채팅방도 물품이 반환 완료되었으면 종료 상태로 취급한다.
        chat=dict(chat)
        chat['completed']=bool(chat['completed'] or chat['status'] in ['찾음','반환 완료'])
        return chat
    # 물품의 대표 사진 주소를 만들고 사진이 없으면 대체 이미지를 사용한다.
    def photo_url(item):
        if not item['photo']:return None
        if item['photo'] in ['wallet.png','samples.png']:return url_for('static',filename='images/'+item['photo'])
        return url_for('upload',filename=item['photo'])
    app.jinja_env.globals['photo_url']=photo_url
    # 물품에 연결된 여러 사진을 표시 순서대로 조회한다.
    def gallery(item):
        rows=get_db().execute('SELECT filename FROM item_photos WHERE item_id=? ORDER BY position,id',(item['id'],)).fetchall()
        if rows:return [url_for('upload',filename=row['filename']) for row in rows]
        return [photo_url(item)] if item['photo'] else []
    # 수정 화면에서 기존 사진의 저장 파일명과 표시용 원래 이름을 조회한다.
    def photo_records(item):
        if not item:return []
        rows=get_db().execute('SELECT filename,original_name FROM item_photos WHERE item_id=? ORDER BY position,id',(item['id'],)).fetchall()
        if rows:return [dict(filename=row['filename'],name=row['original_name'] or row['filename'],url=url_for('upload',filename=row['filename'])) for row in rows]
        return [dict(filename=item['photo'],name=item['photo'],url=photo_url(item))] if item['photo'] else []
    app.jinja_env.globals['photo_records']=photo_records
    app.jinja_env.globals['gallery']=gallery
    # 건물·층·호실 또는 외부 좌표·설명을 검증한다. 두 가지 보관 방식만 허용한다.
    def validate_location(form):
        t=form.get('storage_type','internal')
        if t=='internal':
            b=form.get('building','');f=form.get('floor','');room=form.get('room','').strip()
            if b not in BUILDINGS or f not in BUILDINGS[b]:raise ValueError('건물과 층 선택을 확인해주세요.')
            if not room or len(room)>60:raise ValueError('호실 또는 상세 보관 장소를 60자 이내로 입력해주세요.')
            return dict(storage_type=t,building=b,floor=f,room=room,latitude=None,longitude=None,location_note=None)
        if t!='external':raise ValueError('보관 방식은 건물 내부 또는 외부로 선택해주세요.')
        try:lat=float(form.get('latitude',''));lng=float(form.get('longitude',''))
        except (ValueError,TypeError):raise ValueError('현재 위치 또는 지도에서 외부 위치를 지정해주세요.')
        if not math.isfinite(lat) or not math.isfinite(lng) or not(-90<=lat<=90 and -180<=lng<=180):raise ValueError('좌표가 올바르지 않습니다.')
        note=form.get('location_note','').strip()
        if not note or len(note)>500:raise ValueError('외부 위치 설명을 500자 이내로 입력해주세요.')
        return dict(storage_type=t,building=None,floor=None,room=None,latitude=lat,longitude=lng,location_note=note)
    # 확장자와 실제 이미지 형식·용량을 검사하고, 메타데이터를 제거한 사진을 UUID 이름으로 저장한다.
    def save_photo(files,required=False):
        chosen=[f for f in files if f.filename]
        if len(chosen)>1:raise ValueError('사진은 한 장만 등록할 수 있습니다.')
        if not chosen:
            if required:raise ValueError('습득물 사진 한 장을 등록해주세요.')
            return None
        # 한글 이름이 secure_filename에서 제거되어도 확장자는 유지한다.
        file=chosen[0];ext=Path(file.filename).suffix.lower()
        if ext not in ['.jpg','.jpeg','.png']:raise ValueError('JPG, JPEG, PNG 사진만 등록할 수 있습니다.')
        data=file.read(app.config['MAX_FILE_SIZE']+1)
        if len(data)>app.config['MAX_FILE_SIZE']:raise ValueError('사진은 최대 5MB까지 등록할 수 있습니다.')
        try:
            image=Image.open(io.BytesIO(data));fmt=image.format
            if fmt not in ['JPEG','PNG'] or (fmt=='PNG' and ext!='.png') or (fmt=='JPEG' and ext not in ['.jpg','.jpeg']):raise ValueError('사진 형식과 확장자가 일치하지 않습니다.')
            if image.width*image.height>25_000_000:raise ValueError('사진 해상도가 너무 큽니다.')
            image.verify();image=Image.open(io.BytesIO(data));image.load()
        except (UnidentifiedImageError,OSError,Image.DecompressionBombError):raise ValueError('정상적인 이미지 파일이 아닙니다.')
        # 원본 GPS/EXIF 메타데이터가 공개 사진에서 위치를 누설하지 않도록 재인코딩.
        # UUID 파일명을 secure_filename으로 처리한 결과를 실제 저장명으로 사용한다.
        out=secure_filename(uuid.uuid4().hex+('.png' if fmt=='PNG' else '.jpg'))
        image.convert('RGBA' if fmt=='PNG' else 'RGB').save(Path(app.config['UPLOAD_FOLDER'])/out,format=fmt)
        return out
    # 사진 여러 장의 원본 용량 합계가 5MB 이내인지 검사한다. 중간 실패 시 이미 저장한 파일도 정리한다.
    def save_photos(files):
        chosen=[file for file in files if file.filename]
        total=0
        for file in chosen:
            file.stream.seek(0,2);total+=file.stream.tell();file.stream.seek(0)
        if total>app.config['MAX_FILE_SIZE']:raise ValueError('사진 전체 용량은 합계 5MB까지 등록할 수 있습니다.')
        saved=[]
        try:
            for file in chosen:saved.append(save_photo([file]))
            return saved
        except Exception:
            for name in saved:(Path(app.config['UPLOAD_FOLDER'])/name).unlink(missing_ok=True)
            raise
    @app.route('/login',methods=['GET','POST'])
    # 등록된 테스트 학번으로 로그인하고 사용자 학번을 세션에 저장한다.
    def login():
        if request.method=='POST':
            n=request.form.get('student_no','').strip()
            if not n:flash('학번을 입력해주세요.','error')
            elif not get_db().execute('SELECT 1 FROM students WHERE student_no=?',(n,)).fetchone():flash('등록되지 않은 학번입니다.','error')
            else:session.clear();session['student_no']=n;csrf();return redirect(url_for('home'))
        return render_template('login.html')
    @app.post('/logout')
    @login_required
    # 로그인 세션을 비우고 로그인 화면으로 이동한다.
    def logout():session.clear();return redirect(url_for('login'))
    @app.get('/')
    @login_required
    # 메인 화면에 최근 등록된 물품을 조회해 보여준다.
    def home():return render_template('home.html',items=get_db().execute('SELECT * FROM items WHERE deleted=0 ORDER BY id DESC LIMIT 4').fetchall(),screen='main')
    @app.get('/items')
    @login_required
    # 물품 유형·카테고리·상태로 목록을 필터링한다. 입력값은 SQL의 ?에 별도로 전달한다.
    def items():
        kind=request.args.get('kind','all');category=request.args.get('category','전체');status=request.args.get('status','전체 상태')
        q='SELECT * FROM items WHERE deleted=0';params=[]
        if kind in ['lost','found']:q+=' AND kind=?';params.append(kind)
        if category!='전체':q+=' AND category=?';params.append(category)
        # 완료된 분실물의 기존 DB 상태도 화면의 반환 완료 필터에 포함한다.
        if status=='반환 완료':q+=' AND status IN (?,?)';params.extend(['반환 완료','찾음'])
        elif status!='전체 상태':q+=' AND status=?';params.append(status)
        return render_template('items.html',items=get_db().execute(q+' ORDER BY id DESC',params).fetchall(),screen='item_list',kind=kind,category=category,status=status,chosen=[row['category'] for row in get_db().execute('SELECT category FROM category_subscriptions WHERE student_no=?',(session['student_no'],))],chosen_kinds=subscription_kinds(session['student_no']))
    @app.get('/items/<int:item_id>')
    @login_required
    # 물품 공개 정보를 보여준다. 정확한 보관 위치는 공개 목록에서 노출하지 않는다.
    def detail(item_id):
        item=item_get(item_id)
        # 정확한 위치 필드는 공개 상세 템플릿에 전달하지 않는다.
        public={k:item[k] for k in ['id','owner','kind','title','category','appearance','found_place','extra','photo','photo_pos','status','created_at']}
        return render_template('detail.html',item=public,screen='item_list')
    @app.route('/items/new/<kind>',methods=['GET','POST'])
    @app.route('/items/<int:item_id>/edit',methods=['GET','POST'])
    @login_required
    # 분실물·습득물 등록과 본인 물품 수정을 처리하고 사진 및 위치를 검증한다.
    def item_form(kind=None,item_id=None):
        item=item_get(item_id) if item_id else None
        if item:owner(item);kind=item['kind']
        if kind not in ['lost','found']:abort(404)
        if item and (item['status'] in ['찾음','반환 완료'] or get_db().execute('SELECT 1 FROM location_shares s JOIN chats c ON c.id=s.chat_id WHERE c.item_id=?',(item_id,)).fetchone()):
            flash('위치 공개 또는 반환 완료 이후에는 등록 내용을 수정할 수 없습니다.','error');return redirect(url_for('my_items'))
        values=dict(item) if item else {}
        if request.method=='POST':
            values.update(request.form);photo=None;photos=[]
            try:
                title=request.form.get('title','').strip();category=request.form.get('category','');appearance=request.form.get('appearance','').strip();place=request.form.get('found_place','').strip();extra=request.form.get('extra','').strip()
                if not title or len(title)>100 or not appearance or len(appearance)>500 or len(place)>300 or len(extra)>1000:raise ValueError('물품 이름과 외형 정보를 입력하고 입력 길이를 확인해주세요.')
                if category not in CATEGORIES:raise ValueError('카테고리를 선택해주세요.')
                loc=validate_location(request.form) if kind=='found' else dict.fromkeys(['storage_type','building','floor','room','latitude','longitude','location_note'])
                existing=photo_records(item)
                editing=request.form.get('photo_edit')=='yes'
                requested=request.form.getlist('keep_photo')
                if editing and any(name not in [record['filename'] for record in existing] for name in requested):raise ValueError('사진 선택을 확인해주세요.')
                kept=[record for record in existing if record['filename'] in requested] if editing else []
                files=[file for file in request.files.getlist('photo') if file.filename]
                if editing:
                    total=sum((Path(app.config['UPLOAD_FOLDER'])/record['filename']).stat().st_size for record in kept if (Path(app.config['UPLOAD_FOLDER'])/record['filename']).exists())
                    for file in files:file.stream.seek(0,2);total+=file.stream.tell();file.stream.seek(0)
                    if total>app.config['MAX_FILE_SIZE']:raise ValueError('남긴 사진과 새 사진의 합계는 5MB 이하여야 합니다.')
                photos=save_photos(files)
                final_records=kept+[dict(filename=name,name=Path(file.filename.replace('\\','/')).name) for name,file in zip(photos,files)]
                if not editing and not photos:final_records=existing
                photo=final_records[0]['filename'] if final_records else None
                db=get_db();fields=['title','category','appearance','found_place','extra','photo','storage_type','building','floor','room','latitude','longitude','location_note']
                data=[title,category,appearance,place,extra,photo,*loc.values()]
                with db:
                    if item:
                        db.execute('UPDATE items SET '+','.join(f'{f}=?' for f in fields)+',photo_pos=? WHERE id=? AND owner=?',(*data,item['photo_pos'] if photo==item['photo'] else None,item_id,session['student_no']))
                        new_id=item_id
                    else:
                        new_id=db.execute('INSERT INTO items('+','.join(fields)+',owner,kind,status,created_at) VALUES('+','.join('?' for _ in range(len(fields)+4))+')',(*data,session['student_no'],kind,'찾는 중' if kind=='lost' else '보관 중',now())).lastrowid
                        match_notifications(db,item_get(new_id))
                    if editing or photos:
                        db.execute('DELETE FROM item_photos WHERE item_id=?',(new_id,))
                        db.executemany('INSERT INTO item_photos(item_id,filename,position,original_name) VALUES(?,?,?,?)',[(new_id,record['filename'],index,record['name']) for index,record in enumerate(final_records)])
                if editing:
                    for record in existing:
                        name=record['filename']
                        if name not in [r['filename'] for r in final_records] and not db.execute('SELECT 1 FROM item_photos WHERE filename=? UNION SELECT 1 FROM items WHERE photo=?',(name,name)).fetchone():
                            (Path(app.config['UPLOAD_FOLDER'])/name).unlink(missing_ok=True)
                flash('물품이 저장되었습니다.','success');return redirect(url_for('detail',item_id=new_id))
            except ValueError as e:
                for name in photos:(Path(app.config['UPLOAD_FOLDER'])/name).unlink(missing_ok=True)
                flash(str(e),'error')
        return render_template('item_form.html',screen='lost_reg' if kind=='lost' else 'found_reg',kind=kind,item=item,values=values)
    @app.post('/items/<int:item_id>/delete')
    @login_required
    # 본인 물품만 삭제 표시하여 일반 목록에서 제외한다.
    def delete_item(item_id):
        item=item_get(item_id);owner(item);db=get_db()
        with db:db.execute('UPDATE items SET deleted=1 WHERE id=? AND owner=?',(item_id,session['student_no']))
        flash('등록물이 삭제되었습니다.','success');return redirect(url_for('my_items'))
    @app.get('/my-items')
    @login_required
    # 현재 로그인한 학번이 등록한 물품만 조회한다.
    def my_items():
        kind=request.args.get('kind','all');q='SELECT * FROM items WHERE owner=? AND deleted=0';p=[session['student_no']]
        if kind in ['lost','found']:q+=' AND kind=?';p.append(kind)
        return render_template('my_items.html',items=get_db().execute(q+' ORDER BY id DESC',p).fetchall(),screen='my_items',kind=kind)
    @app.get('/uploads/<filename>')
    @login_required
    # 업로드 폴더 안의 이미지 파일을 안전한 파일 전송 함수로 제공한다.
    def upload(filename):return send_from_directory(app.config['UPLOAD_FOLDER'],filename)
    @app.post('/items/<int:item_id>/chat')
    @login_required
    # 물품 등록자와 연락한 사용자 사이에 채팅방을 생성한다. 기존 방이 있으면 재사용한다.
    def start_chat(item_id):
        item=item_get(item_id);me=session['student_no'];db=get_db()
        if item['owner']==me:abort(400,description='자신의 게시글에는 채팅을 보낼 수 없습니다.')
        existing=db.execute('SELECT id FROM chats WHERE item_id=? AND author=? AND other=?',(item_id,item['owner'],me)).fetchone()
        if existing:
            with db:db.execute('DELETE FROM hidden_chats WHERE student_no=? AND chat_id=?',(me,existing['id']))
            return redirect(url_for('chat',chat_id=existing['id']))
        if item['status'] in ['찾음','반환 완료']:abort(400,description='완료된 물품에는 새 채팅을 만들 수 없습니다.')
        finder=item['owner'] if item['kind']=='found' else me;loser=me if item['kind']=='found' else item['owner']
        with db:
            db.execute('INSERT OR IGNORE INTO chats(item_id,author,other,finder,loser,created_at) VALUES(?,?,?,?,?,?)',(item_id,item['owner'],me,finder,loser,now()))
            cid=db.execute('SELECT id FROM chats WHERE item_id=? AND author=? AND other=?',(item_id,item['owner'],me)).fetchone()['id']
            notify(db,item['owner'],'chat',cid,'새 채팅방이 생성되었습니다.',chat_id=cid)
        return redirect(url_for('chat',chat_id=cid))
    # 본인에게 보이는 채팅방·메시지·공개된 위치를 모아 화면에 전달한다.
    def chat_context(cid=None):
        me=session['student_no'];db=get_db()
        rooms=db.execute('SELECT c.*,i.title,i.photo,i.photo_pos FROM chats c JOIN items i ON i.id=c.item_id WHERE (c.author=? OR c.other=?) AND NOT EXISTS(SELECT 1 FROM hidden_chats h WHERE h.chat_id=c.id AND h.student_no=?) ORDER BY c.id DESC',(me,me,me)).fetchall()
        if not cid and rooms:cid=rooms[0]['id']
        current=chat_get(cid) if cid else None
        messages=db.execute('SELECT * FROM messages WHERE chat_id=? ORDER BY id',(cid,)).fetchall() if cid else []
        share=db.execute('SELECT * FROM location_shares WHERE chat_id=?',(cid,)).fetchone() if cid else None
        linked_candidates=db.execute("SELECT id,title FROM items WHERE owner=? AND kind='lost' AND status='찾는 중' AND deleted=0 ORDER BY id DESC",(current['loser'],)).fetchall() if current and current['finder']==me else []
        if cid:
            with db:db.execute('UPDATE notifications SET is_read=1 WHERE recipient=? AND chat_id=?',(me,cid))
        return dict(rooms=rooms,current=current,messages=messages,share=share,linked_candidates=linked_candidates,screen='chat')
    @app.get('/chat')
    @app.get('/chat/<int:chat_id>')
    @login_required
    # 채팅방 목록과 선택한 대화 내용을 표시한다.
    def chat(chat_id=None):return render_template('chat.html',**chat_context(chat_id))
    @app.get('/api/chats/<int:chat_id>/messages')
    @login_required
    # 채팅 당사자에게 최신 메시지와 위치 공개 상태를 JSON으로 제공한다.
    def poll_messages(chat_id):
        c=chat_get(chat_id);db=get_db();share=db.execute('SELECT * FROM location_shares WHERE chat_id=?',(chat_id,)).fetchone()
        msgs=[dict(m) for m in db.execute('SELECT * FROM messages WHERE chat_id=? ORDER BY id',(chat_id,)).fetchall()]
        return jsonify(messages=msgs,shared=bool(share),completed=bool(c['completed']))
    @app.post('/chat/<int:chat_id>/messages')
    @login_required
    # 메시지를 저장하고 상대방에게 해당 메시지 이벤트의 인앱 알림을 생성한다.
    def send_message(chat_id):
        c=chat_get(chat_id);text=request.form.get('body','').strip()
        if c['completed'] or c['deleted']:abort(400,description='종료된 채팅입니다.')
        if not text or len(text)>2000:abort(400,description='메시지를 1~2000자로 입력해주세요.')
        db=get_db();me=session['student_no'];recipient=c['other'] if me==c['author'] else c['author']
        with db:
            mid=db.execute('INSERT INTO messages(chat_id,sender,body,created_at) VALUES(?,?,?,?)',(chat_id,me,text,now())).lastrowid
            notify(db,recipient,'message',mid,'새로운 메시지가 도착했습니다.',chat_id=chat_id)
        return redirect(url_for('chat',chat_id=chat_id))
    @app.route('/chat/<int:chat_id>/location',methods=['GET','POST'])
    @login_required
    # 습득자가 특징 확인을 완료한 뒤 위치를 공개한다. 분실물 게시글에서는 위치를 직접 지정한다.
    def share_location(chat_id):
        c=chat_get(chat_id);me=session['student_no'];db=get_db()
        if me!=c['finder']:abort(403)
        if c['deleted'] or c['completed'] or c['status'] in ['찾음','반환 완료']:abort(400,description='완료 또는 삭제된 물품입니다.')
        current_share=db.execute('SELECT 1 FROM location_shares WHERE chat_id=?',(chat_id,)).fetchone()
        if current_share:return redirect(url_for('chat',chat_id=chat_id))
        item=item_get(c['item_id']);values=dict(item) if c['kind']=='found' else {}
        candidates=db.execute("SELECT id,title,category FROM items WHERE owner=? AND kind='lost' AND status='찾는 중' AND deleted=0",(c['loser'],)).fetchall() if c['kind']=='found' else []
        if request.method=='POST':
            values.update(request.form)
            try:
                if request.form.get('confirmed')!='yes':raise ValueError('채팅으로 주인을 확인한 뒤 확인 항목을 선택해주세요.')
                loc={k:item[k] for k in ['storage_type','building','floor','room','latitude','longitude','location_note']} if c['kind']=='found' else validate_location(request.form)
                linked=None
                if c['kind']=='found' and request.form.get('linked_lost_id'):
                    try:linked=int(request.form['linked_lost_id'])
                    except ValueError:raise ValueError('연결할 분실물을 확인해주세요.')
                    valid=db.execute("SELECT 1 FROM items WHERE id=? AND owner=? AND kind='lost' AND status='찾는 중' AND deleted=0 AND category=?",(linked,c['loser'],item['category'])).fetchone()
                    if not valid:raise ValueError('상대방의 같은 카테고리 분실물만 연결할 수 있습니다.')
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    if db.execute('SELECT completed FROM chats WHERE id=?',(chat_id,)).fetchone()['completed']:
                        raise ValueError('종료된 채팅에는 위치를 공개할 수 없습니다.')
                    cur=db.execute('INSERT OR IGNORE INTO location_shares(chat_id,sender,storage_type,building,floor,room,latitude,longitude,location_note,linked_lost_id,shared_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(chat_id,me,*loc.values(),linked,now()))
                    if cur.rowcount:
                        db.execute('INSERT INTO messages(chat_id,sender,body,created_at) VALUES(?,?,?,?)',(chat_id,me,'주인을 확인하여 보관 위치를 공개했습니다.',now()))
                        notify(db,c['loser'],'location',chat_id,'보관 위치가 공개되었습니다.',chat_id=chat_id)
                return redirect(url_for('chat',chat_id=chat_id))
            except ValueError as e:flash(str(e),'error')
        return render_template('share_location.html',current=c,values=values,candidates=candidates,screen='chat')
    @app.post('/chat/<int:chat_id>/reject')
    @login_required
    # 습득자만 위치 공개 전 대화를 거절 종료한다. 물품 상태와 보관 위치는 변경하지 않는다.
    def reject_location(chat_id):
        db=get_db();me=session['student_no']
        try:
            # 위치 공개와 거절이 동시에 들어와도 트랜잭션 안에서 최신 상태를 확인한다.
            db.execute('BEGIN IMMEDIATE')
            c=chat_get(chat_id)
            if me!=c['finder']:abort(403)
            if c['rejected']:
                db.rollback()
                return redirect(url_for('chat',chat_id=chat_id))
            if c['completed'] or c['deleted']:abort(400,description='종료 또는 삭제된 채팅입니다.')
            if db.execute('SELECT 1 FROM location_shares WHERE chat_id=?',(chat_id,)).fetchone():
                abort(400,description='이미 공개한 위치는 거절로 취소할 수 없습니다.')
            db.execute('UPDATE chats SET completed=1,rejected=1 WHERE id=?',(chat_id,))
            db.execute('INSERT INTO messages(chat_id,sender,body,created_at) VALUES(?,?,?,?)',(chat_id,me,'위치 공개를 거절하여 채팅을 종료했습니다.',now()))
            notify(db,c['loser'],'rejected',chat_id,'위치 공개가 거절되어 채팅이 종료되었습니다.',chat_id=chat_id)
            db.commit()
        except Exception:
            db.rollback()
            raise
        return redirect(url_for('chat',chat_id=chat_id))
    @app.post('/chat/<int:chat_id>/complete')
    @login_required
    # 위치 공개 후 분실자가 반환 완료를 누르면 연결된 물품 상태와 대화 완료 상태를 함께 갱신한다.
    def complete(chat_id):
        c=chat_get(chat_id);db=get_db();me=session['student_no']
        if c['loser']!=me:abort(403)
        if c['completed']:return redirect(url_for('chat',chat_id=chat_id))
        share=db.execute('SELECT * FROM location_shares WHERE chat_id=?',(chat_id,)).fetchone()
        if not share:abort(400,description='위치를 공개하고 물품 전달을 마친 뒤 완료해주세요.')
        if c['deleted']:abort(400,description='삭제된 물품입니다.')
        found_id=c['item_id'] if c['kind']=='found' else None
        lost_id=share['linked_lost_id'] if c['kind']=='found' else c['item_id']
        # 예약/반환 경쟁: 트랜잭션 안에서 상태를 재검사한다.
        try:
            db.execute('BEGIN IMMEDIATE')
            for i in [found_id,lost_id]:
                if i:
                    row=db.execute('SELECT status,deleted FROM items WHERE id=?',(i,)).fetchone()
                    if not row or row['deleted'] or row['status'] in ['찾음','반환 완료']:raise ValueError('다른 채팅에서 이미 완료되었거나 삭제된 물품입니다.')
            db.execute('INSERT INTO returns(chat_id,finder,loser,found_item_id,lost_item_id,completed_at) VALUES(?,?,?,?,?,?)',(chat_id,c['finder'],c['loser'],found_id,lost_id,now()))
            if found_id:db.execute("UPDATE items SET status='반환 완료' WHERE id=?",(found_id,))
            if lost_id:db.execute("UPDATE items SET status='찾음' WHERE id=?",(lost_id,))
            # 반환된 물품에 연결된 모든 채팅방을 종료해 추가 대화와 위치 공개를 막는다.
            completed_ids=[i for i in [found_id,lost_id] if i]
            db.execute('UPDATE chats SET completed=1 WHERE item_id IN ('+','.join('?' for _ in completed_ids)+')',completed_ids)
            db.execute('INSERT INTO messages(chat_id,sender,body,created_at) VALUES(?,?,?,?)',(chat_id,me,'물품 반환이 완료되었습니다.',now()))
            notify(db,c['finder'],'complete',chat_id,'물품 반환이 완료되었습니다.',chat_id=chat_id);db.commit()
        except ValueError as e:db.rollback();flash(str(e),'error')
        return redirect(url_for('chat',chat_id=chat_id))
    @app.get('/chat/<int:chat_id>/map')
    @login_required
    # 위치 공개를 받은 분실자만 외부 보관 위치의 지도 화면에 접근할 수 있다.
    def location_map(chat_id):
        c=chat_get(chat_id);share=get_db().execute('SELECT * FROM location_shares WHERE chat_id=?',(chat_id,)).fetchone()
        if not share or session['student_no']!=c['loser']:abort(403)
        return render_template('map.html',share=share,current=c,screen='chat')
    @app.post('/chat/<int:chat_id>/delete')
    @login_required
    # 현재 사용자에게만 채팅방을 숨긴다. 상대방의 대화 기록은 삭제하지 않는다.
    def delete_chat(chat_id):
        chat_get(chat_id) # 당사자만 자신의 목록에서 삭제할 수 있다.
        with get_db():
            get_db().execute('INSERT OR IGNORE INTO hidden_chats(student_no,chat_id) VALUES(?,?)',(session['student_no'],chat_id))
        return redirect(url_for('chat'))
    @app.get('/api/notifications/recent')
    @login_required
    # 로그인 사용자 자신의 최근 알림을 헤더 알림창에 제공한다.
    def recent_notifications():
        rows=get_db().execute('SELECT id,body,created_at,is_read FROM notifications WHERE recipient=? ORDER BY id DESC LIMIT 5',(session['student_no'],)).fetchall()
        return jsonify(notifications=[dict(row) for row in rows])

    @app.get('/api/chat/<int:chat_id>/route')
    @login_required
    # 공개된 목적지를 DB에서 읽어 외부 도보 경로 API를 requests로 호출한다. 응답 JSON을 검증하고 실패를 안내한다.
    def navigation_route(chat_id):
        # 목적지는 요청값을 신뢰하지 않고, 공개된 해당 채팅의 DB 좌표를 사용한다.
        c=chat_get(chat_id)
        share=get_db().execute('SELECT * FROM location_shares WHERE chat_id=?',(chat_id,)).fetchone()
        if c['loser']!=session['student_no'] or not share or share['storage_type']!='external':abort(403)
        try:
            lat=float(request.args.get('latitude',''));lng=float(request.args.get('longitude',''))
            if not math.isfinite(lat) or not math.isfinite(lng) or not -90<=lat<=90 or not -180<=lng<=180:raise ValueError()
        except (TypeError,ValueError):return jsonify(error='현재 위치 좌표가 올바르지 않습니다.'),400
        # Python requests로 외부 API에 요청하고 JSON 경로 데이터를 받는다.
        url=f"https://routing.openstreetmap.de/routed-foot/route/v1/driving/{lng},{lat};{share['longitude']},{share['latitude']}"
        try:
            response=requests.get(url,params={'overview':'full','geometries':'geojson'},headers={'User-Agent':'CampusLostFoundCourseProject/1.0'},timeout=(5,15),allow_redirects=False)
            response.raise_for_status()
            if response.status_code!=200:raise ValueError('Unexpected response')
            data=response.json()
            if data.get('code') in ['NoRoute','NoSegment']:
                return jsonify(error='현재 위치와 목적지 사이의 도보 경로를 지도 데이터에서 찾지 못했습니다. 위치 핀이 실제 통로 근처인지 확인해주세요.',error_code=data['code']),422
            route=data['routes'][0]
            coords=route['geometry']['coordinates']
            if data.get('code')!='Ok' or route['geometry']['type']!='LineString' or len(coords)<2:raise ValueError('No route')
            for point in coords:
                if len(point)!=2 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in point) or not -180<=point[0]<=180 or not -90<=point[1]<=90:raise ValueError('Invalid geometry')
            for key in ['distance','duration']:
                if not isinstance(route[key],(int,float)) or not math.isfinite(route[key]) or route[key]<0:raise ValueError('Invalid summary')
            return jsonify(code='Ok',routes=[{'geometry':route['geometry'],'distance':route['distance'],'duration':route['duration']}])
        except requests.Timeout:
            return jsonify(error='경로 서버의 응답 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.',error_code='timeout'),502
        except requests.exceptions.SSLError:
            return jsonify(error='경로 서버의 보안 인증서 연결에 실패했습니다. 실행 PC의 날짜와 네트워크 설정을 확인해주세요.',error_code='ssl'),502
        except requests.ConnectionError:
            return jsonify(error='경로 서버에 연결할 수 없습니다. 인터넷 연결 또는 학교 네트워크의 접속 제한을 확인해주세요.',error_code='connection'),502
        except requests.HTTPError as e:
            status=e.response.status_code if e.response is not None else 0
            return jsonify(error=f'경로 서버가 요청을 거부했습니다. HTTP {status}. 잠시 후 다시 시도해주세요.',error_code='http'),502
        except (requests.RequestException,ValueError,KeyError,IndexError,TypeError,AttributeError):
            return jsonify(error='경로 서버의 응답 형식이 올바르지 않습니다. 잠시 후 다시 시도해주세요.',error_code='invalid_response'),502

    # 사용자가 구독한 알림의 물품 유형을 조회한다.
    def subscription_kinds(me):
        rows=get_db().execute('SELECT kinds FROM category_subscriptions WHERE student_no=?',(me,)).fetchall()
        return sorted({kind for row in rows for kind in row['kinds'].split(',')}) if rows else ['found']
    @app.route('/notifications/settings',methods=['GET','POST'])
    @login_required
    # 여러 카테고리와 분실물·습득물 알림 조건을 검증하여 저장한다.
    def notification_settings():
        db=get_db();me=session['student_no']
        if request.method=='POST':
            chosen=set(request.form.getlist('categories'))
            kinds=set(request.form.getlist('kinds')) if request.form.get('kind_settings') else {'found'}
            if not kinds.issubset({'lost','found'}):abort(400,description='물품 종류를 확인해주세요.')
            if chosen and not kinds:abort(400,description='분실물 또는 습득물을 선택해주세요.')
            if not chosen.issubset(set(CATEGORIES)):abort(400,description='카테고리를 확인해주세요.')
            with db:
                db.execute('DELETE FROM category_subscriptions WHERE student_no=?',(me,))
                db.executemany('INSERT INTO category_subscriptions(student_no,category,kinds) VALUES(?,?,?)',[(me,c,','.join(sorted(kinds))) for c in CATEGORIES if c in chosen])
            flash('알림 카테고리가 저장되었습니다.','success')
            if request.form.get('source')=='items':return redirect(url_for('items',kind=request.form.get('kind','all'),category=request.form.get('category','전체'),status=request.form.get('status','전체 상태')))
            return redirect(url_for('notification_settings'))
        chosen=[row['category'] for row in db.execute('SELECT category FROM category_subscriptions WHERE student_no=?',(me,))]
        return render_template('notification_settings.html',chosen=chosen,chosen_kinds=subscription_kinds(me),screen='notifications')
    @app.get('/notifications')
    @login_required
    # 로그인 사용자 자신의 전체 알림 내역을 조회한다.
    def notifications():
        rows=get_db().execute('SELECT * FROM notifications WHERE recipient=? ORDER BY id DESC',(session['student_no'],)).fetchall()
        return render_template('notifications.html',notifications=rows,screen='notifications')
    @app.post('/notifications/<int:nid>/open')
    @login_required
    # 본인 알림인지 확인하고 읽음 처리한 뒤 관련 물품 또는 채팅으로 이동한다.
    def notification_open(nid):
        db=get_db();n=db.execute('SELECT * FROM notifications WHERE id=? AND recipient=?',(nid,session['student_no'])).fetchone()
        if not n:abort(403)
        with db:db.execute('UPDATE notifications SET is_read=1 WHERE id=? AND recipient=?',(nid,session['student_no']))
        # 삭제된 물품 알림은 404 상세 화면 대신 안내 후 목록으로 이동한다.
        target=db.execute('SELECT i.deleted FROM items i WHERE i.id=?',(n['item_id'],)).fetchone() if n['item_id'] else None
        if n['chat_id']:
            target=db.execute('SELECT i.deleted FROM chats c JOIN items i ON i.id=c.item_id WHERE c.id=?',(n['chat_id'],)).fetchone()
        if not target or target['deleted']:
            flash('삭제된 물품입니다.','error')
            return redirect(url_for('items'))
        return redirect(url_for('chat',chat_id=n['chat_id']) if n['chat_id'] else url_for('detail',item_id=n['item_id']))
    @app.post('/notifications/read-all')
    @login_required
    # 현재 사용자의 알림만 모두 읽음으로 변경한다.
    def read_all():
        db=get_db()
        with db:db.execute('UPDATE notifications SET is_read=1 WHERE recipient=?',(session['student_no'],))
        return redirect(url_for('notifications'))
    @app.get('/api/notifications/count')
    @login_required
    # 현재 사용자의 읽지 않은 알림 수를 JSON으로 반환한다.
    def notification_count():return jsonify(count=get_db().execute('SELECT COUNT(*) FROM notifications WHERE recipient=? AND is_read=0',(session['student_no'],)).fetchone()[0])
    @app.post('/mypage/name')
    @login_required
    # 표시용 이름만 변경한다. 로그인에 사용하는 학번은 변경하지 않는다.
    def change_name():
        name=request.form.get('display_name','').strip()
        if not name or len(name)>30:
            flash('표시 이름을 1~30자로 입력해주세요.','error')
        else:
            db=get_db()
            with db:db.execute('UPDATE students SET display_name=? WHERE student_no=?',(name,session['student_no']))
            flash('이름이 변경되었습니다.','success')
        return redirect(url_for('mypage'))
    @app.get('/mypage')
    @login_required
    # 본인 등록물 수와 진행 중인 채팅방 수를 DB에서 계산해 마이페이지에 전달한다.
    def mypage():
        profile=get_db().execute('SELECT display_name FROM students WHERE student_no=?',(session['student_no'],)).fetchone()
        # 현재 사용자 소유의 등록물만 집계하고 삭제된 글은 제외한다.
        counts={'lost':0,'found':0}
        for row in get_db().execute('SELECT kind,COUNT(*) AS total FROM items WHERE owner=? AND deleted=0 GROUP BY kind',(session['student_no'],)):
            counts[row['kind']]=row['total']
        # 반환 완료되거나 삭제된 물품의 채팅은 활성 채팅방에서 제외한다.
        active_chats=get_db().execute('SELECT COUNT(*) FROM chats c JOIN items i ON i.id=c.item_id WHERE (c.author=? OR c.other=?) AND c.completed=0 AND i.deleted=0 AND i.status NOT IN (?,?) AND NOT EXISTS(SELECT 1 FROM hidden_chats h WHERE h.chat_id=c.id AND h.student_no=?)',(session['student_no'],session['student_no'],'찾음','반환 완료',session['student_no'])).fetchone()[0]
        return render_template('mypage.html',screen='mypage',display_name=profile['display_name'] or session['student_no'],item_counts=counts,active_chats=active_chats)
    @app.errorhandler(413)
    # 요청 용량 제한을 넘으면 서버 오류 대신 업로드 제한 안내를 표시한다.
    def large(e):return render_template('error.html',message='사진 전체 용량은 합계 5MB까지 업로드할 수 있습니다.',screen=''),413
    for code in [400,403,404,500]:
        def handler(e,code=code):
            msg={400:'요청 내용을 확인해주세요.',403:'접근 권한이 없습니다.',404:'대상을 찾을 수 없습니다.',500:'처리 중 문제가 생겼습니다. 다시 시도해주세요.'}[code]
            if code==400 and getattr(e,'description',None):msg=e.description
            if request.path.startswith('/api/'):return jsonify(error=msg),code
            return render_template('error.html',message=msg,screen=''),code
        app.register_error_handler(code,handler)
    return app

if __name__=='__main__':
    create_app().run(host='127.0.0.1',port=int(os.getenv('PORT','5000')),debug=False)
