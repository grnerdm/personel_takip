# personel_takip.py

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, current_app, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import FlaskForm
from wtforms import (
    StringField, SubmitField, TextAreaField, FileField, BooleanField,
    DateField, IntegerField, SelectMultipleField, SelectField, FloatField
)
from wtforms.validators import (
    DataRequired, Email, Length, Regexp, Optional
)
from flask_wtf.file import MultipleFileField, FileAllowed, FileRequired
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import RequestEntityTooLarge
from flask_migrate import Migrate
from functools import wraps
from datetime import datetime
from werkzeug.datastructures import FileStorage
import os
import shutil
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
from flask import send_from_directory
import uuid
import re
import sqlalchemy as sa
import json
import pycountry  # Ülke kodları için
import pytz
from weasyprint import HTML
from flask_login import LoginManager, login_required, current_user, login_user, logout_user
from flask_login import UserMixin
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy import func

# Uygulama ve Veritabanı Yapılandırması
app = Flask(__name__, instance_relative_config=True)
# Güçlü ve rastgele bir anahtar
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + \
    os.path.join(app.instance_path, 'personel_takip.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['FLAGS_JS'] = 'https://cdnjs.cloudflare.com/ajax/libs/flag-icon-css/6.6.6/css/flag-icons.min.css'
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5 MB
app.config['WTF_CSRF_ENABLED'] = True  # CSRF korumasını etkinleştir
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static/uploads')
app.config['BABEL_DEFAULT_LOCALE'] = 'tr'  # Varsayılan dil Türkçe
# Desteklenen diller: Türkçe, İngilizce, Hollandaca, Lehçe
app.config['BABEL_SUPPORTED_LOCALES'] = ['tr', 'en', 'nl', 'pl']

# 'static/uploads' klasörünü oluştur
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Yedekleme Klasörünün Tanımlanması
BACKUP_DIR = os.path.join(app.root_path, 'backups')
if not os.path.exists(BACKUP_DIR):
    os.makedirs(BACKUP_DIR)


def backup_database():
    """
    Veritabanını yedekler ve yedek dosyasını 'backups' klasörüne kaydeder.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    db_file = os.path.join(app.instance_path, 'personel_takip.db')  # Doğru yol
    backup_filename = f'backup_{timestamp}.db'
    backup_path = os.path.join(BACKUP_DIR, backup_filename)
    try:
        shutil.copy(db_file, backup_path)
        print(f"Veritabanı başarıyla yedeklendi: {backup_path}")
    except Exception as e:
        print(f"Yedekleme sırasında hata oluştu: {e}")


# Zamanlanmış Görevler İçin Scheduler
scheduler = BackgroundScheduler()

# Haftalık Yedekleme Görevi Ekleme
scheduler.add_job(backup_database, 'interval', weeks=1,
                  next_run_time=datetime.now())

# Haftalık kira yenileme görevi


def create_next_week_payments():
    """Her hafta başında otomatik olarak çalışacak fonksiyon"""
    with app.app_context():  # Uygulama bağlamı eklendi
        current_year, current_week = get_current_year_week()

        # Tüm ev-personel ilişkilerini al
        house_personeller = HousePersonel.query.all()

        for hp in house_personeller:
            # Bir sonraki hafta için kira kaydı var mı kontrol et
            next_payment = RentPayment.query.filter_by(
                house_id=hp.house_id,
                personel_id=hp.personel_id,
                year=current_year,
                week_number=current_week
            ).first()

            if not next_payment:
                # Yeni kira kaydı oluştur
                new_payment = RentPayment(
                    house_id=hp.house_id,
                    personel_id=hp.personel_id,
                    year=current_year,
                    week_number=current_week,
                    weekly_rent=hp.weekly_rent,
                    paid=False
                )
                db.session.add(new_payment)

        try:
            db.session.commit()
            print(f"{current_year} yılı {
                  current_week}. hafta kira kayıtları oluşturuldu.")
        except Exception as e:
            db.session.rollback()
            print(f"Hata oluştu: {e}")


# Scheduler'a haftalık görevi ekle
scheduler.add_job(
    create_next_week_payments,
    'cron',
    day_of_week='mon',  # Her pazartesi
    hour=0,
    minute=0
)

# Scheduler'ı Başlatma
scheduler.start()

# Flask uygulaması kapanırken scheduler'ı durdurmak için
atexit.register(lambda: scheduler.shutdown())

# SQLAlchemy'yi başlatma
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Flask-Login Ayarları
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'  # Giriş sayfasının adı


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# İzin verilen dosya uzantıları
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# Dosya uzantısı kontrolü


def allowed_file(filename, allowed_extensions=ALLOWED_EXTENSIONS):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in allowed_extensions

# Büyük dosya yüklemeleri için hata işleyicisi


@app.errorhandler(RequestEntityTooLarge)
def handle_large_file(error):
    flash('Yüklemeye çalıştığınız dosya çok büyük. Maksimum dosya boyutu 5 MB olmalıdır.')
    return redirect(request.url)


# Association Table for Personel and Gonderim
gonderim_personel = db.Table('gonderim_personel',
                             db.Column('gonderim_id', db.Integer, db.ForeignKey(
                                 'gonderim.id'), primary_key=True),
                             db.Column('personel_id', db.Integer, db.ForeignKey(
                                 'personel.id'), primary_key=True)
                             )

# Ülke seçeneklerini oluşturma fonksiyonu


def get_country_choices():
    countries = [(country.alpha_2.lower(), f"{country.name} ({country.alpha_2})")
                 for country in pycountry.countries]
    return sorted(countries, key=lambda x: x[1])


# Haftanın Yıl ve Hafta Numarasını Almak İçin Yardımcı Fonksiyon
def get_current_year_week():
    today = datetime.today()
    year, week_num, weekday = today.isocalendar()
    return year, week_num


# Model Sınıfları


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Log(db.Model):
    __tablename__ = 'logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        # ondelete parametresi eklendi
        db.ForeignKey('users.id', ondelete='CASCADE'),
        nullable=False
    )
    action = db.Column(db.String(255), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # İlişkiler
    user = db.relationship(
        'User',
        # cascade parametresi eklendi
        backref=db.backref('logs', lazy=True, cascade='all, delete-orphan'),
        passive_deletes=True  # passive_deletes eklendi
    )

    def __repr__(self):
        return f"<Log {self.action} by User ID {self.user_id} at {self.timestamp}>"


class Isyeri(db.Model):
    __tablename__ = 'isyeri'
    id = db.Column(db.Integer, primary_key=True)
    ad = db.Column(db.String(100), nullable=False)
    kvk_nummer = db.Column(db.String(100), nullable=False)  # KVK Numarası
    sahib = db.Column(db.String(100), nullable=False)  # Şirket Sahibi
    iletisim_telefon = db.Column(
        db.String(20), nullable=False)  # İletişim Telefonu
    iletisim_email = db.Column(db.String(100))  # İletişim E-maili
    faaliyet_sektoru = db.Column(
        db.String(100), nullable=False)  # Faaliyet Sektörü
    banka_hesabi = db.Column(db.String(50), nullable=False)  # Banka Hesabı
    adres = db.Column(db.String(200))  # Adres
    gonderimler = db.relationship('Gonderim', backref='isyeri', lazy=True)


class Personel(db.Model):
    @property
    def is_active(self):
        # Personel bir gönderimde atanmışsa aktif kabul edelim
        return any(gonderim for gonderim in self.gonderimler)

    __tablename__ = 'personel'
    id = db.Column(db.Integer, primary_key=True)
    ad = db.Column(db.String(100), nullable=False)
    soyad = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=True)
    telefon = db.Column(db.String(20), nullable=True)
    adres = db.Column(db.String(200), nullable=True)
    banka_hesabi = db.Column(db.String(50), nullable=True)
    bilgi = db.Column(db.String(500), nullable=True)
    bsn_numarasi = db.Column(db.String(9), nullable=True)
    foto_path = db.Column(db.String(200), nullable=True)
    kimlik_foto_path = db.Column(db.String(200), nullable=True)
    uyruk = db.Column(db.String(2), nullable=False)  # ISO 3166-1 alpha-2 kodu
    calistigi_sirket_id = db.Column(db.Integer, db.ForeignKey(
        'isyeri.id', ondelete='SET NULL'), nullable=True)
    status = db.Column(db.String(10), nullable=False, default='Pasif')

    def update_status(self):
        """Personelin durumunu günceller"""
        if self.aktif_izin:
            self.status = 'Pasif'
        elif self.calistigi_sirket_id:
            self.status = 'Aktif'
        else:
            self.status = 'Pasif'

    @property
    def aktif_izin(self):
        """Personelin aktif iznini döndürür"""
        return IzinKaydi.query.filter_by(
            personel_id=self.id,
            durum='aktif'
        ).first()

    @property
    def izin_hakki_var(self):
        """Personelin izin hakkının olup olmadığını kontrol eder"""
        ayarlar = IzinAyarlari.query.first()
        if not ayarlar:
            return False

        toplam_saat = calculate_total_working_hours(self.id)
        calisma_suresi = calculate_working_duration(self)

        if not calisma_suresi:
            return False

        return (toplam_saat >= ayarlar.saat_esigi or
                calisma_suresi['total_months'] >= ayarlar.ay_esigi)

    @property
    def kalan_izin_gunu(self):
        """Personelin kalan izin günü sayısını hesaplar"""
        if not self.izin_hakki_var:
            return 0

        ayarlar = IzinAyarlari.query.first()
        if not ayarlar:
            return 0

        # Kullanılan izin günlerini hesapla
        kullanilan_izinler = IzinKaydi.query.filter_by(
            personel_id=self.id,
            izin_turu='yillik',
            durum='tamamlandi'
        ).all()

        kullanilan_gun = sum(izin.get_total_days()
                             for izin in kullanilan_izinler)
        return ayarlar.izin_gun_sayisi - kullanilan_gun

    @property
    def calisma_suresi_str(self):
        """Personelin çalışma süresini string olarak döndürür"""
        sure = calculate_working_duration(self)
        if not sure:
            return "Çalışma süresi hesaplanamadı"
        return f"{sure['years']} yıl {sure['months']} ay {sure['days']} gün"

    @property
    def toplam_calisma_saati(self):
        """Personelin toplam çalışma saatini döndürür"""
        return calculate_total_working_hours(self.id)

    @property
    def is_active(self):
        return self.status == 'Aktif' and self.calistigi_sirket_id is not None

    # İlişkiler
    gonderimler = db.relationship(
        'Gonderim', secondary=gonderim_personel, back_populates='personeller')
    araclar = db.relationship('Vehicle', backref='sofor', lazy=True)
    salaries = db.relationship('Salary', backref='personel', lazy=True)
    house_personeller = db.relationship(
        'HousePersonel', back_populates='personel')
    calistigi_sirket = db.relationship(
        'Isyeri',
        backref=db.backref('personeller', lazy=True),
        foreign_keys=[calistigi_sirket_id]
    )


# Araç Modülü
class Vehicle(db.Model):
    __tablename__ = 'vehicle'
    id = db.Column(db.Integer, primary_key=True)
    arac_plaka = db.Column(db.String(20), unique=True, nullable=False)
    arac_marka_model = db.Column(db.String(100), nullable=False)
    sofor_id = db.Column(db.Integer, db.ForeignKey(
        'personel.id'), nullable=True)

    # Mevcut Alanlar
    km = db.Column(db.Integer, default=0)
    previous_km = db.Column(db.Integer, default=0)  # Son bakım km'si
    # True: Bakım yapıldı, False: Bakım gerekli
    maintenance_status = db.Column(db.Boolean, default=True)
    parking_zone = db.Column(db.String(100))
    ruhsat_photo_path = db.Column(db.String(200))
    # True: Ceza var, False: Ceza yok
    penalty_status = db.Column(db.Boolean, default=False)
    penalty_info = db.Column(db.Text)

    # Yeni Alanlar
    muayne_date = db.Column(db.Date, nullable=True)
    next_muayne_date = db.Column(db.Date, nullable=True)

    # İlişkiler
    gonderimler = db.relationship('Gonderim', backref='arac', lazy=True)
    logs = db.relationship('VehicleLog', backref='vehicle', lazy=True)


class VehicleLog(db.Model):
    __tablename__ = 'vehicle_log'
    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey(
        'vehicle.id'), nullable=False)
    # Örneğin: 'bakim_guncelleme'
    change_type = db.Column(db.String(50), nullable=False)
    old_value = db.Column(db.String(200))
    new_value = db.Column(db.String(200))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<VehicleLog {self.change_type} for Vehicle ID {self.vehicle_id}>"


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(50), nullable=False)


class Income(db.Model):
    __tablename__ = 'income'
    id = db.Column(db.Integer, primary_key=True)
    isyeri_id = db.Column(db.Integer, db.ForeignKey(
        'isyeri.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(255))

    # İlişkiler
    isyeri = db.relationship('Isyeri', backref='incomes')


class HousePersonel(db.Model):
    __tablename__ = 'house_personel'
    id = db.Column(db.Integer, primary_key=True)
    house_id = db.Column(db.Integer, db.ForeignKey('house.id'))
    personel_id = db.Column(db.Integer, db.ForeignKey('personel.id'))
    belediye_adres_kaydi = db.Column(db.Boolean, default=False)

    # Yeni alan
    # Bu personelin haftalık kira payı
    weekly_rent = db.Column(db.Float, default=0.0)

    house = db.relationship("House", back_populates="house_personeller")
    personel = db.relationship("Personel", back_populates="house_personeller")


class House(db.Model):
    __tablename__ = 'house'
    id = db.Column(db.Integer, primary_key=True)
    address = db.Column(db.String(200), nullable=False)
    capacity = db.Column(db.Integer, nullable=False)
    insurance_status = db.Column(db.Boolean, default=False)
    insurance_date = db.Column(db.Date, nullable=True)
    vve_meeting_date = db.Column(db.Date, nullable=True)
    insurance_documents = db.Column(db.Text)  # JSON list of document paths
    photos = db.Column(db.Text)  # JSON list of photo paths

    # Yeni Alanlar
    # 'company' veya 'rental'
    house_type = db.Column(db.String(10), default='company')
    rental_price = db.Column(db.Float, nullable=True)  # Haftalık kira bedeli
    rental_contract_path = db.Column(
        db.String(200), nullable=True)  # Kira kontratı dosya yolu

    house_personeller = db.relationship(
        'HousePersonel', back_populates='house')

    @property
    def current_residents(self):
        return len(self.house_personeller)

    @property
    def insurance_documents_list(self):
        return json.loads(self.insurance_documents) if self.insurance_documents else []

    @property
    def photos_list(self):
        return json.loads(self.photos) if self.photos else []


class RentPayment(db.Model):
    __tablename__ = 'rent_payment'
    id = db.Column(db.Integer, primary_key=True)
    house_id = db.Column(db.Integer, db.ForeignKey('house.id'), nullable=False)
    personel_id = db.Column(db.Integer, db.ForeignKey(
        'personel.id'), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    week_number = db.Column(db.Integer, nullable=False)
    paid = db.Column(db.Boolean, default=False)
    payment_date = db.Column(db.DateTime)  # Ödeme tarihi
    updated_by = db.Column(db.Integer, db.ForeignKey(
        'users.id'))  # Güncelleyen kullanıcı
    weekly_rent = db.Column(db.Float, nullable=False, default=0.0)

    # İlişkiler
    house = db.relationship(
        'House', backref=db.backref('rent_payments', lazy=True))
    personel = db.relationship(
        'Personel', backref=db.backref('rent_payments', lazy=True))
    user = db.relationship('User', backref=db.backref(
        'rent_payment_updates', lazy=True))

    def __repr__(self):
        return f"<RentPayment {self.id} - House ID {self.house_id} - Personel ID {self.personel_id}>"


class RentLog(db.Model):
    __tablename__ = 'rent_log'
    id = db.Column(db.Integer, primary_key=True)
    house_id = db.Column(db.Integer, db.ForeignKey('house.id'), nullable=False)
    personel_id = db.Column(db.Integer, db.ForeignKey(
        'personel.id'), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    week_number = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(50), nullable=False)  # 'paid' veya 'unpaid'
    amount = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey(
        'users.id'), nullable=False)  # İşlemi yapan kullanıcı

    # İlişkiler
    house = db.relationship('House', backref='rent_logs')
    personel = db.relationship('Personel', backref='rent_logs')
    user = db.relationship('User', backref='rent_logs')


class AddHouseForm(FlaskForm):
    address = StringField('Ev Adresi', validators=[
                          DataRequired(), Length(max=200)])
    capacity = IntegerField('Kapasite', validators=[DataRequired()])
    insurance_status = BooleanField('Sigorta Durumu')
    insurance_date = DateField('Sigorta Tarihi', validators=[Optional()])
    vve_meeting_date = DateField(
        'VVE Toplantı Tarihi', validators=[Optional()])
    photos = MultipleFileField('Fotoğraflar', validators=[Optional(), FileAllowed(
        ['jpg', 'jpeg', 'png'], 'Sadece JPG, JPEG ve PNG dosyaları!')])
    insurance_documents = MultipleFileField('Sigorta Belgeleri (PDF)', validators=[
                                            Optional(), FileAllowed(['pdf'], 'Sadece PDF dosyaları!')])
    residents = SelectMultipleField(
        'Evde Yaşayan Personeller', coerce=int, validators=[Optional()])

    # Yeni Alanlar
    house_type = SelectField('Ev Tipi',
                             choices=[('company', 'Şirkete Ait'),
                                      ('rental', 'Kiralık')],
                             validators=[DataRequired()])
    rental_price = FloatField('Kira Bedeli (€)', validators=[Optional()])
    rental_contract = FileField('Kira Kontratı (PDF)', validators=[
                                Optional(), FileAllowed(['pdf'])])

    submit = SubmitField('Ev Ekle')


class EditHouseForm(FlaskForm):
    address = StringField('Ev Adresi', validators=[
                          DataRequired(), Length(max=200)])
    capacity = IntegerField('Kapasite', validators=[DataRequired()])
    insurance_status = BooleanField('Sigorta Durumu')
    insurance_date = DateField('Sigorta Tarihi', validators=[Optional()])
    vve_meeting_date = DateField(
        'VVE Toplantı Tarihi', validators=[Optional()])
    photos = MultipleFileField('Fotoğraflar', validators=[Optional()])
    insurance_documents = MultipleFileField(
        'Sigorta Belgeleri (PDF)', validators=[Optional()])

    # Yeni Alanlar

    house_type = SelectField('Ev Tipi',
                             choices=[('company', 'Şirkete Ait'),
                                      ('rental', 'Kiralık')],
                             validators=[DataRequired()])
    rental_price = FloatField('Kira Bedeli (€)', validators=[Optional()])
    rental_contract = FileField('Kira Kontratı (PDF)', validators=[
                                Optional(), FileAllowed(['pdf'])])

    submit = SubmitField('Güncelle')


class Expense(db.Model):
    __tablename__ = 'expense'
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(255))


class Salary(db.Model):
    __tablename__ = 'salary'
    id = db.Column(db.Integer, primary_key=True)
    personel_id = db.Column(db.Integer, db.ForeignKey(
        'personel.id'), nullable=False)
    brut_saatlik_ucret = db.Column(db.Float, nullable=True)
    standart_saatler = db.Column(db.Float, nullable=False)
    hafta_sonu_saatleri = db.Column(db.Float, default=0)
    gece_saatleri = db.Column(db.Float, default=0)
    tatil_saatleri = db.Column(db.Float, default=0)
    toplam_brut = db.Column(db.Float, nullable=False)
    gelir_vergisi = db.Column(db.Float, nullable=False)
    sosyal_guvenlik = db.Column(db.Float, nullable=False)
    toplam_kesintiler = db.Column(db.Float, nullable=False)
    net_gelir = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, nullable=False)

# Paylaşma Gonderim Tablosu


class Gonderim(db.Model):
    __tablename__ = 'gonderim'
    id = db.Column(db.Integer, primary_key=True)
    isyeri_id = db.Column(
        db.Integer, db.ForeignKey('isyeri.id'), nullable=True)
    arac_id = db.Column(db.Integer, db.ForeignKey('vehicle.id'), nullable=True)
    tarih = db.Column(db.String(100))
    share_token = db.Column(db.String(36), unique=True,
                            nullable=True)  # UUID4 için 36 karakter

    # İlişkiler
    personeller = db.relationship(
        'Personel', secondary=gonderim_personel, back_populates='gonderimler')

    def generate_share_token(self):
        if not self.share_token:
            self.share_token = str(uuid.uuid4())
            db.session.commit()

    def personel_eklenebilir(self, personel):
        """Personelin gönderime eklenip eklenemeyeceğini kontrol eder"""
        if personel.aktif_izin:
            return False, "Personel izinde olduğu için gönderime eklenemez."
        if personel.status != 'Aktif':
            return False, "Sadece aktif personeller gönderime eklenebilir."
        return True, ""

    def personel_ekle(self, personel):
        """Personeli gönderime ekler"""
        eklenebilir, mesaj = self.personel_eklenebilir(personel)
        if not eklenebilir:
            raise ValueError(mesaj)
        if personel not in self.personeller:
            self.personeller.append(personel)
            return True
        return False


class GonderimLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    gonderim_id = db.Column(db.Integer, db.ForeignKey('gonderim.id'))
    personel_id = db.Column(db.Integer, db.ForeignKey('personel.id'))
    islem_tipi = db.Column(db.String(20))  # 'eklendi' veya 'çıkarıldı'
    tarih = db.Column(db.DateTime, default=datetime.utcnow)

    # İlişkiler
    gonderim = db.relationship('Gonderim', backref='logs')
    personel = db.relationship('Personel')


class IzinAyarlari(db.Model):
    __tablename__ = 'izin_ayarlari'
    id = db.Column(db.Integer, primary_key=True)
    saat_esigi = db.Column(db.Integer, nullable=False)  # Örn: 500 saat
    ay_esigi = db.Column(db.Integer, nullable=False)    # Örn: 6 ay
    izin_gun_sayisi = db.Column(db.Integer, nullable=False)  # Örn: 20 gün
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class IzinKaydi(db.Model):
    __tablename__ = 'izin_kayitlari'
    id = db.Column(db.Integer, primary_key=True)
    personel_id = db.Column(db.Integer, db.ForeignKey(
        'personel.id'), nullable=False)
    baslangic_tarihi = db.Column(db.Date, nullable=False)
    bitis_tarihi = db.Column(db.Date, nullable=False)
    # 'yillik', 'hastalik', 'vefat' vb.
    izin_turu = db.Column(db.String(50), nullable=False)
    # 'aktif', 'tamamlandi', 'iptal'
    durum = db.Column(db.String(20), nullable=False)
    aciklama = db.Column(db.String(500))
    olusturma_tarihi = db.Column(db.DateTime, default=datetime.utcnow)
    olusturan_id = db.Column(
        db.Integer, db.ForeignKey('users.id'), nullable=False)

    # İlişkiler
    personel = db.relationship('Personel', backref='izin_kayitlari')
    olusturan = db.relationship('User', backref='olusturulan_izinler')

    def get_total_days(self):
        """İzin süresini gün olarak hesaplar"""
        if not self.baslangic_tarihi or not self.bitis_tarihi:
            return 0
        return (self.bitis_tarihi - self.baslangic_tarihi).days + 1


@event.listens_for(IzinKaydi, 'after_insert')
def update_personel_after_izin(mapper, connection, target):
    """İzin kaydı eklendiğinde personeli güncelle"""
    personel = Personel.query.get(target.personel_id)
    if personel:
        if target.durum == 'aktif':
            # Sadece şirket ID'sini null yap
            personel.calistigi_sirket_id = None
        db.session.commit()


def calculate_total_working_hours(personel_id):
    """Personelin toplam çalışma saatini hesaplar"""
    total_hours = db.session.query(
        func.sum(Salary.standart_saatler +
                 Salary.hafta_sonu_saatleri +
                 Salary.gece_saatleri +
                 Salary.tatil_saatleri)
    ).filter(Salary.personel_id == personel_id).scalar()

    return total_hours or 0


def calculate_working_duration(personel):
    """Personelin işe başlama tarihinden itibaren çalışma süresini hesaplar"""
    if not personel.calistigi_sirket_id:
        return None

    # İlk maaş kaydını bul
    first_salary = Salary.query.filter_by(
        personel_id=personel.id).order_by(Salary.date).first()
    if not first_salary:
        return None

    start_date = first_salary.date
    today = datetime.now().date()

    years = today.year - start_date.year
    months = today.month - start_date.month
    days = today.day - start_date.day

    if days < 0:
        months -= 1
        days += 30
    if months < 0:
        years -= 1
        months += 12

    return {
        'years': years,
        'months': months,
        'days': days,
        'total_months': years * 12 + months
    }

# Form sınıfları


class IzinAyarlariForm(FlaskForm):
    saat_esigi = IntegerField('Saat Eşiği',
                              validators=[DataRequired()],
                              description='İzin hakkı için gerekli minimum çalışma saati')
    ay_esigi = IntegerField('Ay Eşiği',
                            validators=[DataRequired()],
                            description='İzin hakkı için gerekli minimum çalışma ayı')
    izin_gun_sayisi = IntegerField('İzin Gün Sayısı',
                                   validators=[DataRequired()],
                                   description='Hak kazanılan izin gün sayısı')
    guncelle = SubmitField('Ayarları Güncelle')


class IzinEkleForm(FlaskForm):
    baslangic_tarihi = DateField(
        'Başlangıç Tarihi', validators=[DataRequired()])
    bitis_tarihi = DateField('Bitiş Tarihi', validators=[DataRequired()])
    izin_turu = SelectField('İzin Türü',
                            choices=[
                                ('yillik', 'Yıllık İzin'),
                                ('hastalik', 'Hastalık İzni'),
                                ('vefat', 'Vefat İzni'),
                                ('diger', 'Diğer')
                            ],
                            validators=[DataRequired()])
    aciklama = TextAreaField('Açıklama')
    kaydet = SubmitField('İzin Ekle')

# Maaş Hesaplama Formu


class MaasHesaplamaForm(FlaskForm):
    personel_id = SelectField('Personel', coerce=int,
                              validators=[DataRequired()])
    brut_saatlik_ucret = FloatField(
        'Brüt Saatlik Ücret (€)', validators=[DataRequired()])
    standart_saatler = FloatField(
        'Standart Saatler', validators=[DataRequired()])
    hafta_sonu_saatleri = FloatField('Hafta Sonu Saatleri', default=0)
    gece_saatleri = FloatField('Gece Saatleri', default=0)
    tatil_saatleri = FloatField('Resmi Tatil Saatleri', default=0)
    date = DateField('Tarih', validators=[DataRequired()])
    submit = SubmitField('Maaş Hesapla')

# Edit Gonderim Formu


class EditGonderimForm(FlaskForm):
    isyeri = SelectField('İş Yeri', coerce=int, validators=[DataRequired()])
    arac = SelectField('Araç', coerce=int, validators=[DataRequired()])
    personeller = SelectMultipleField(
        'Gönderilecek Personeller', coerce=int, validators=[DataRequired()])
    sofor = SelectField('Şoför', coerce=int, choices=[],
                        validators=[DataRequired()])
    submit = SubmitField('Güncelle')


def update_personel_status(personel):
    # Eğer iş yeri ID varsa aktif, yoksa pasif
    if personel.calistigi_sirket_id and personel.calistigi_sirket_id != 0:
        personel.status = 'Aktif'
    else:
        personel.status = 'Pasif'


# Veritabanını Oluşturma ve Varsayılan Yönetici Hesabı Ekleme
with app.app_context():
    db.create_all()
    # Varsayılan yönetici hesabı oluşturma
    admin_username = 'admin'
    admin_password = generate_password_hash(
        'admin123')  # Daha güçlü bir şifre tercih edin
    admin_role = 'admin'

    if not User.query.filter_by(username=admin_username).first():
        admin_user = User(username=admin_username,
                          password=admin_password, role=admin_role)
        db.session.add(admin_user)
        db.session.commit()

# BSN Numarası Doğrulama Fonksiyonu


def is_valid_bsn(bsn):
    return re.fullmatch(r'\d{9}', bsn) is not None

# Flask-WTF Formları


class EditPersonelForm(FlaskForm):
    ad = StringField('Ad', validators=[DataRequired(), Length(max=100)])
    soyad = StringField('Soyad', validators=[DataRequired(), Length(max=100)])
    email = StringField('E-mail', validators=[Email(), Length(max=100)])
    telefon = StringField('Telefon', validators=[Length(max=20)])
    adres = StringField('Adres', validators=[Length(max=200)])
    banka_hesabi = StringField('Banka Hesabı', validators=[Length(max=50)])
    calistigi_sirket_id = SelectField(
        'Çalıştığı İş Yeri', coerce=int, validators=[Optional()])
    bilgi = TextAreaField('Bilgi', validators=[Length(max=500)])
    bsn_numarasi = StringField('BSN Numarası', validators=[
        Regexp(
            r'^\d{9}$', message="BSN Numarası 9 haneli rakamlardan oluşmalıdır."),
    ])
    foto_path = FileField('Fotoğraf')
    kimlik_foto_path = FileField('Kimlik Fotoğrafı')
    uyruk = SelectField('Uyruk', choices=get_country_choices(),
                        validators=[DataRequired()])
    status = SelectField('Çalışma Durumu', choices=[
                         ('Aktif', 'Aktif'), ('Pasif', 'Pasif')], default='Pasif')
    submit = SubmitField('Güncelle')

    # Yeni alanlar: Ev seçimi ve haftalık kira
    # Ev seçimi için tüm evleri listeleyelim
    house_id = SelectField('Kaldığı Ev', coerce=int, validators=[Optional()])
    weekly_rent = FloatField('Haftalık Ev Kirası', validators=[Optional()])

    submit = SubmitField('Güncelle')

# Edit İşyeri Formu


class EditIsyeriForm(FlaskForm):
    ad = StringField('İş Yeri Adı', validators=[
                     DataRequired(), Length(max=100)])
    kvk_nummer = StringField('KVK Numarası', validators=[
                             DataRequired(), Length(max=100)])
    sahib = StringField('Şirket Sahibi', validators=[
                        DataRequired(), Length(max=100)])
    iletisim_telefon = StringField('İletişim Telefonu', validators=[
                                   DataRequired(), Length(max=20)])
    iletisim_email = StringField(
        'İletişim E-maili', validators=[Email(), Length(max=100), Optional()])
    faaliyet_sektoru = StringField('Faaliyet Sektörü', validators=[
                                   DataRequired(), Length(max=100)])
    banka_hesabi = StringField('Banka Hesabı', validators=[
                               DataRequired(), Length(max=50)])
    adres = StringField('Adres', validators=[Length(max=200), Optional()])
    submit = SubmitField('Güncelle')


# Log Fonksiyonları ve Decorator
@app.template_filter('country_name')
def country_name_filter(alpha_2_code):
    country = pycountry.countries.get(alpha_2=alpha_2_code)
    if country:
        return country.name
    else:
        return alpha_2_code


@app.template_filter('country_flag')
def country_flag_filter(alpha_2_code):
    if alpha_2_code:
        return alpha_2_code.lower()
    return ''


def create_log(user_id, action):
    new_log = Log(user_id=user_id, action=action)
    db.session.add(new_log)
    db.session.commit()


def log_action(action_func):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Loglamayı fonksiyon çalıştırılmadan önce yap
            action = action_func(current_user=current_user, **kwargs)
            if current_user.is_authenticated and action:
                create_log(current_user.id, action)
            response = f(*args, **kwargs)
            return response
        return decorated_function
    return decorator

# Kullanıcı Girişi


@app.route('/login', methods=['GET', 'POST'])
@log_action(lambda current_user, **kwargs: "Login Attempt")
def login():
    if request.method == 'POST':

        username = request.form['username']
        password = request.form['password']

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password, password):
            login_user(user)  # Flask-Login ile kullanıcıyı oturuma ekle
            flash('Giriş başarılı.', 'success')
            create_log(current_user.id, "Giriş Yaptı")
            return redirect(url_for('index'))
        else:
            flash('Kullanıcı adı veya şifre yanlış.', 'danger')
            # Başarısız giriş denemesini loglayabilirsiniz
            return redirect(url_for('login'))
    else:
        return render_template('login.html')

# Kullanıcı Çıkışı


@app.route('/logout')
@login_required
def logout():
    # Loglamayı oturum kapatmadan önce yap
    create_log(current_user.id, f"{current_user.username} çıkış yaptı.")
    logout_user()  # Flask-Login ile oturumu kapat
    flash('Çıkış yaptınız.', 'info')
    return redirect(url_for('login'))

# Tarih ve Saat Fonksiyonları


@app.context_processor
def inject_current_time():
    tz = pytz.timezone('Europe/Amsterdam')
    current_time = datetime.now(tz).strftime('%d/%m/%Y %H:%M:%S')
    year, week_num = get_current_year_week()
    return {'current_time': current_time, 'current_year': year, 'current_week': week_num}

# Her Template haftayı aktarma


@app.context_processor
def inject_current_time():
    now = datetime.now()
    year, week_number, _ = now.isocalendar()
    return {'current_week': week_number, 'current_year': year}

# AJAX ile Arama Rotası


@app.route('/search_personel')
@login_required
def search_personel():
    query = request.args.get('q', '')
    personeller = Personel.query.filter(
        or_(
            Personel.ad.ilike(f'%{query}%'),
            Personel.soyad.ilike(f'%{query}%')
        )
    ).all()
    return jsonify([{
        'id': p.id,
        'ad': p.ad,
        'soyad': p.soyad
    } for p in personeller])


@app.route('/search', methods=['GET'])
def search():
    if not current_user.is_authenticated:
        return jsonify({'error': 'Unauthorized'}), 401

    tab = request.args.get('tab', '').strip()

    if tab == 'isyeri':
        isyeri_search = request.args.get('isyeri_search', '').strip()
        if isyeri_search:
            isyeri_listesi = Isyeri.query.filter(
                Isyeri.ad.ilike(f"%{isyeri_search}%")).all()
        else:
            isyeri_listesi = Isyeri.query.all()

        # İş Yeri Çalışan Sayısını Hesaplama
        gonderimler = Gonderim.query.all()
        isyeri_dict = {isyeri.id: {
            'ad': isyeri.ad,
            'email': isyeri.iletisim_email,
            'adres': isyeri.adres,
            'calisan_sayisi': 0
        } for isyeri in isyeri_listesi}

        for gonderim in gonderimler:
            if gonderim.isyeri_id in isyeri_dict:
                isyeri_dict[gonderim.isyeri_id]['calisan_sayisi'] += len(
                    gonderim.personeller) + (1 if gonderim.arac else 0)

        # Render edilen HTML'i döndür
        rendered = render_template(
            'partials/_isyeri_table.html', isyeri_listesi=isyeri_listesi, isyeri_dict=isyeri_dict)
        return jsonify({'html': rendered})

    elif tab == 'personel':
        personel_search = request.args.get('personel_search', '').strip()
        if personel_search:
            personel_listesi = Personel.query.filter(
                (Personel.ad.ilike(f"%{personel_search}%")) |
                (Personel.soyad.ilike(f"%{personel_search}%"))
            ).all()
        else:
            personel_listesi = Personel.query.all()

        # Render edilen HTML'i döndür
        rendered = render_template(
            'partials/_personel_table.html', personel_listesi=personel_listesi)
        return jsonify({'html': rendered})

    elif tab == 'arac':
        arac_search = request.args.get('arac_search', '').strip()
        if arac_search:
            arac_listesi = Vehicle.query.join(Personel).filter(
                (Vehicle.arac_plaka.ilike(f"%{arac_search}%")) |
                (Vehicle.arac_marka_model.ilike(f"%{arac_search}%")) |
                (Personel.ad.ilike(f"%{arac_search}%")) |
                (Personel.soyad.ilike(f"%{arac_search}%"))
            ).all()
        else:
            arac_listesi = Vehicle.query.all()

        # Render edilen HTML'i döndür
        rendered = render_template(
            'partials/_arac_table.html', arac_listesi=arac_listesi)
        return jsonify({'html': rendered})

    elif tab == 'gonderim':
        gonderim_search = request.args.get('gonderim_search', '').strip()
        if gonderim_search:
            gonderilenler = Gonderim.query.join(Isyeri, Gonderim.isyeri).join(Vehicle, Gonderim.arac).join(Personel, Vehicle.sofor).filter(
                (Isyeri.ad.ilike(f"%{gonderim_search}%")) |
                (Vehicle.arac_plaka.ilike(f"%{gonderim_search}%")) |
                (Vehicle.arac_marka_model.ilike(f"%{gonderim_search}%")) |
                (Personel.ad.ilike(f"%{gonderim_search}%")) |
                (Personel.soyad.ilike(f"%{gonderim_search}%"))
            ).all()
        else:
            gonderilenler = Gonderim.query.all()

    elif tab == 'ev':  # Yeni Eklenen
        ev_search = request.args.get('ev_search', '').strip()
        if ev_search:
            ev_listesi = House.query.filter(
                House.address.ilike(f"%{ev_search}%")).all()
        else:
            ev_listesi = House.query.all()

        # Evde Yatan Personellerin Sözlüğünü Oluşturma
        ev_personel_dict = {
            house.id: [
                f"{hp.personel.ad} {hp.personel.soyad}"
                for hp in house.house_personeller
            ]
            for house in ev_listesi
        }

        # Render edilen HTML'i döndür
        rendered = render_template(
            'partials/_ev_table.html', ev_listesi=ev_listesi, ev_personel_dict=ev_personel_dict)
        return jsonify({'html': rendered})

    else:
        return jsonify({'error': 'Invalid tab parameter'}), 400

# Çalışan Sayısı görüntüleme


def get_isyeri_calisan_sayisi(isyeri_id):
    return Personel.query.filter_by(calistigi_sirket_id=isyeri_id).count()

# Anasayfa - Personel, Araç, İş Yeri ve Gönderim Listesi


@app.route('/')
@login_required
@log_action(lambda current_user, **kwargs: "Anasayfa erişimi")
def index():
    if current_user.role not in ['admin', 'manager']:
        flash('Bu sayfaya erişim yetkiniz yok.', 'danger')
        return redirect(url_for('login'))

    # İlk sayfa yüklemesi için aktif sekme parametresi
    active_tab = request.args.get('tab', 'isyeri')

    # Arama terimlerini al
    isyeri_search = request.args.get('isyeri_search', '').strip()
    personel_search = request.args.get('personel_search', '').strip()
    arac_search = request.args.get('arac_search', '').strip()
    gonderim_search = request.args.get('gonderim_search', '').strip()
    ev_search = request.args.get('ev_search', '').strip()

    # Veri çekme işlemleri
    if ev_search:
        ev_listesi = House.query.filter(
            House.address.ilike(f"%{ev_search}%")).all()
    else:
        ev_listesi = House.query.all()

    if isyeri_search:
        isyeri_listesi = Isyeri.query.filter(
            Isyeri.ad.ilike(f"%{isyeri_search}%")).all()
    else:
        isyeri_listesi = Isyeri.query.all()

    if personel_search:
        personel_listesi = Personel.query.filter(
            (Personel.ad.ilike(f"%{personel_search}%")) |
            (Personel.soyad.ilike(f"%{personel_search}%"))
        ).all()
    else:
        personel_listesi = Personel.query.all()

    if arac_search:
        arac_listesi = Vehicle.query.join(Personel).filter(
            (Vehicle.arac_plaka.ilike(f"%{arac_search}%")) |
            (Vehicle.arac_marka_model.ilike(f"%{arac_search}%")) |
            (Personel.ad.ilike(f"%{arac_search}%")) |
            (Personel.soyad.ilike(f"%{arac_search}%"))
        ).all()
    else:
        arac_listesi = Vehicle.query.all()

    if gonderim_search:
        gonderilenler = Gonderim.query.join(Isyeri, Gonderim.isyeri).join(Vehicle, Gonderim.arac).join(Personel, Vehicle.sofor).filter(
            (Isyeri.ad.ilike(f"%{gonderim_search}%")) |
            (Vehicle.arac_plaka.ilike(f"%{gonderim_search}%")) |
            (Vehicle.arac_marka_model.ilike(f"%{gonderim_search}%")) |
            (Personel.ad.ilike(f"%{gonderim_search}%")) |
            (Personel.soyad.ilike(f"%{gonderim_search}%"))
        ).all()
    else:
        gonderilenler = Gonderim.query.all()

    # İş Yeri Çalışan Sayısını Hesaplama
    gonderimler = Gonderim.query.all()
    isyeri_dict = {
        isyeri.id: {
            'ad': isyeri.ad,
            'email': isyeri.iletisim_email,
            'adres': isyeri.adres,
            'calisan_sayisi': 0
        } for isyeri in isyeri_listesi
    }

    for gonderim in gonderimler:
        if gonderim.isyeri_id in isyeri_dict:
            isyeri_dict[gonderim.isyeri_id]['calisan_sayisi'] += len(
                gonderim.personeller) + (1 if gonderim.arac else 0)

            isyeri_listesi = Isyeri.query.all()
            isyeri_dict = {
                isyeri.id: {
                    'ad': isyeri.ad,
                    'calisan_sayisi': get_isyeri_calisan_sayisi(isyeri.id)
                } for isyeri in isyeri_listesi
            }

    ev_listesi = House.query.all()

    # Evde Yatan Personellerin Sözlüğünü Oluşturma
    ev_personel_dict = {
        house.id: [
            f"{hp.personel.ad} {hp.personel.soyad}"
            for hp in house.house_personeller
        ]
        for house in ev_listesi
    }

    return render_template(
        'index.html',
        isyeri_listesi=isyeri_listesi,
        personel_listesi=personel_listesi,
        arac_listesi=arac_listesi,
        gonderilenler=gonderilenler,
        active_tab=active_tab,
        isyeri_search=isyeri_search,
        personel_search=personel_search,
        arac_search=arac_search,
        gonderim_search=gonderim_search,
        ev_search=ev_search,
        isyeri_dict=isyeri_dict,
        ev_listesi=ev_listesi,
        ev_personel_dict=ev_personel_dict
    )


class EditVehicleForm(FlaskForm):
    arac_marka_model = StringField(
        'Araç Marka/Model', validators=[DataRequired(), Length(max=100)])
    km = IntegerField('Araç Km', validators=[DataRequired()])
    maintenance_status = BooleanField('Bakım Yapıldı')
    parking_zone = StringField('Park Bölgesi', validators=[
                               Optional(), Length(max=100)])
    penalty_status = BooleanField('Ceza Var')
    penalty_info = TextAreaField('Ceza Bilgisi', validators=[Optional()])
    muayne_date = DateField('Son Muayne Tarihi', validators=[Optional()])
    next_muayne_date = DateField(
        'Sonraki Muayne Tarihi', validators=[Optional()])
    ruhsat_foto = FileField('Ruhsat Fotoğrafı', validators=[Optional()])
    submit = SubmitField('Güncelle')


@app.route('/edit_vehicle/<int:vehicle_id>', methods=['GET', 'POST'])
@login_required
@log_action(lambda current_user, **kwargs: f"Araç Düzenleme: Araç ID {kwargs.get('vehicle_id', 0)}")
def edit_vehicle(vehicle_id):
    if current_user.role not in ['admin', 'manager']:
        flash('Bu sayfaya erişim yetkiniz yok.', 'danger')
        return redirect(url_for('index'))

    vehicle = Vehicle.query.get(vehicle_id)
    if not vehicle:
        flash('Araç bulunamadı.')
        return redirect(url_for('index'))

    form = EditVehicleForm(obj=vehicle)  # Formu mevcut araç verileriyle doldur

    if form.validate_on_submit():
        # KM Güncelleme
        old_km = vehicle.km
        new_km = form.km.data
        vehicle.km = new_km

        # Kullanıcının bakım yapıldı işaretlemesi var mı?
        if form.maintenance_status.data:
            vehicle.maintenance_status = True
            vehicle.previous_km = vehicle.km  # previous_km'yi güncelle
            flash('Bakım başarıyla yapıldı.', 'success')
            # Bakım güncelleme logu
            log_entry = VehicleLog(
                vehicle_id=vehicle.id,
                change_type='bakim_guncelleme',
                old_value=str(False),
                new_value=str(True)
            )
            db.session.add(log_entry)
        else:
            # Km farkını kontrol etme ve bakım durumunu güncelleme
            km_difference = vehicle.km - vehicle.previous_km
            if km_difference >= 15000:
                vehicle.maintenance_status = False
                flash('Araç bakımı gerekli! Km farkı 15,000\'i aştı.', 'warning')
                # Bakım durumu logu
                log_entry = VehicleLog(
                    vehicle_id=vehicle.id,
                    change_type='bakim_guncelleme',
                    old_value=str(vehicle.maintenance_status),
                    new_value=str(False)
                )
                db.session.add(log_entry)
            else:
                vehicle.maintenance_status = True

        # Ceza Durumu Güncelleme
        old_penalty_status = vehicle.penalty_status
        old_penalty_info = vehicle.penalty_info
        vehicle.penalty_status = form.penalty_status.data
        vehicle.penalty_info = form.penalty_info.data

        if old_penalty_status != vehicle.penalty_status or old_penalty_info != vehicle.penalty_info:
            log_entry = VehicleLog(
                vehicle_id=vehicle.id,
                change_type='ceza_guncelleme',
                old_value=f"Durum: {old_penalty_status}, Bilgi: {
                    old_penalty_info}",
                new_value=f"Durum: {vehicle.penalty_status}, Bilgi: {
                    vehicle.penalty_info}"
            )
            db.session.add(log_entry)

        # Muayne Tarihleri Güncelleme
        old_muayne_date = vehicle.muayne_date
        old_next_muayne_date = vehicle.next_muayne_date
        vehicle.muayne_date = form.muayne_date.data
        vehicle.next_muayne_date = form.next_muayne_date.data

        if old_muayne_date != vehicle.muayne_date or old_next_muayne_date != vehicle.next_muayne_date:
            log_entry = VehicleLog(
                vehicle_id=vehicle.id,
                change_type='muayne_guncelleme',
                old_value=f"Muayne Tarihi: {
                    old_muayne_date}, Sonraki Muayne Tarihi: {old_next_muayne_date}",
                new_value=f"Muayne Tarihi: {vehicle.muayne_date}, Sonraki Muayne Tarihi: {
                    vehicle.next_muayne_date}"
            )
            db.session.add(log_entry)

        # Ruhsat Fotoğrafı Yükleme
        ruhsat_foto = form.ruhsat_foto.data
        if ruhsat_foto:
            if allowed_file(ruhsat_foto.filename):
                filename = secure_filename(ruhsat_foto.filename)
                extension = os.path.splitext(filename)[1]
                ruhsat_foto_filename = f"{uuid.uuid4()}{extension}"
                ruhsat_foto_path = os.path.join(
                    current_app.config['UPLOAD_FOLDER'], ruhsat_foto_filename)
                ruhsat_foto.save(ruhsat_foto_path)
                # Eski ruhsat fotoğrafını silme
                if vehicle.ruhsat_photo_path:
                    eski_ruhsat = os.path.join(
                        current_app.root_path, vehicle.ruhsat_photo_path)
                    if os.path.exists(eski_ruhsat):
                        os.remove(eski_ruhsat)
                # Yeni yolu güncelleme
                vehicle.ruhsat_photo_path = os.path.join(
                    'static/uploads', ruhsat_foto_filename)
                # Log ruhsat fotoğrafı güncelleme
                log_entry = VehicleLog(
                    vehicle_id=vehicle.id,
                    change_type='ruhsat_guncelleme',
                    old_value=vehicle.ruhsat_photo_path,
                    new_value=vehicle.ruhsat_photo_path
                )
                db.session.add(log_entry)
            else:
                flash(
                    'Geçersiz dosya türü. Sadece png, jpg, jpeg ve gif dosyalarına izin verilir.', 'danger')
                return redirect(request.url)

        try:
            db.session.commit()
            flash('Araç bilgileri başarıyla güncellendi.', 'success')
            # Log km update
            log_entry = VehicleLog(
                vehicle_id=vehicle.id,
                change_type='km_guncelleme',
                old_value=str(old_km),
                new_value=str(new_km)
            )
            db.session.add(log_entry)
            db.session.commit()
            return redirect(url_for('edit_vehicle', vehicle_id=vehicle_id))
        except Exception as e:
            db.session.rollback()
            flash(f'Araç bilgileri güncellenirken bir hata oluştu: {
                  e}', 'danger')
            return redirect(request.url)

    # GET isteği için
    return render_template('edit_vehicle.html',
                           vehicle=vehicle,
                           form=form)


@app.route('/edit_gonderim/<int:gonderim_id>', methods=['GET', 'POST'])
@login_required
@log_action(lambda current_user, **kwargs: f"Gönderim Düzenleme: Gönderim ID {kwargs.get('gonderim_id', 0)}")
def edit_gonderim(gonderim_id):
    if current_user.role not in ['admin', 'manager']:
        flash('Bu sayfaya erişim yetkiniz yok.', 'danger')
        return redirect(url_for('index'))

    gonderim = db.session.get(Gonderim, gonderim_id)
    if not gonderim:
        flash('Gönderim bulunamadı.')
        return redirect(url_for('index'))

    form = EditGonderimForm()

    # Form seçeneklerini hazırla
    form.isyeri.choices = [(isyeri.id, isyeri.ad)
                           for isyeri in Isyeri.query.all()]
    form.arac.choices = [(arac.id, f"{
                          arac.arac_plaka} - {arac.arac_marka_model}") for arac in Vehicle.query.all()]
    form.sofor.choices = [(0, 'Şoför Yok')] + [
        (personel.id, f"{personel.ad} {personel.soyad}")
        for personel in Personel.query.filter(
            Personel.id.in_(db.session.query(Vehicle.sofor_id).filter(
                Vehicle.sofor_id.isnot(None)))
        ).all()
    ]

    # Şoför olmayan ve ya bu gönderimde olan personelleri getir
    sofor_ids = db.session.query(Vehicle.sofor_id).filter(
        Vehicle.sofor_id.isnot(None)).all()
    sofor_ids = [id[0] for id in sofor_ids if id[0] is not None]
    mevcut_personel_ids = [p.id for p in gonderim.personeller]

    personeller = Personel.query.filter(
        (
            (Personel.calistigi_sirket_id.is_(None)) |  # Çalışmayan
            (Personel.id.in_(mevcut_personel_ids))      # veya bu gönderimde olan
        ) &
        ~Personel.id.in_(sofor_ids) &                   # şoför olmayan
        ~Personel.id.in_(                               # ve izinde olmayan
            db.session.query(IzinKaydi.personel_id)
            .filter(IzinKaydi.durum == 'aktif')
        )
    ).all()

    form.personeller.choices = [
        (p.id, f"{p.ad} {p.soyad}") for p in personeller]

    if request.method == 'GET':
        # Mevcut verileri forma yükle
        form.isyeri.data = gonderim.isyeri_id
        form.arac.data = gonderim.arac_id
        form.personeller.data = [
            p.id for p in gonderim.personeller if p.id not in sofor_ids]
        form.sofor.data = gonderim.arac.sofor_id if gonderim.arac and gonderim.arac.sofor_id else 0

    if form.validate_on_submit():
        try:
            # Eski personelleri kaydet
            eski_personeller = set(
                p.id for p in gonderim.personeller if p.id not in sofor_ids)
            yeni_personeller = set(form.personeller.data)

            # Eklenen ve çıkarılan personelleri belirle
            eklenenler = yeni_personeller - eski_personeller
            cikarilanlar = eski_personeller - yeni_personeller

            hatali_personeller = []

            # Çıkarılan personelleri güncelle
            for personel_id in cikarilanlar:
                personel = Personel.query.get(personel_id)
                if personel:
                    gonderim.personeller.remove(personel)
                    personel.calistigi_sirket_id = None
                    db.session.add(personel)

                    # Log kaydı
                    log = GonderimLog(
                        gonderim_id=gonderim.id,
                        personel_id=personel.id,
                        islem_tipi='cikarildi',
                        tarih=datetime.utcnow()
                    )
                    db.session.add(log)
                    print(f"DEBUG - Personel çıkarıldı: ID={personel.id}")

            # Eklenen personelleri güncelle
            for personel_id in eklenenler:
                personel = Personel.query.get(personel_id)
                if personel:
                    # İzin kontrolü
                    if personel.aktif_izin:
                        hatali_personeller.append(
                            f"{personel.ad} {personel.soyad} (İzinde)")
                        continue

                    # Başka iş yerinde çalışma kontrolü
                    if personel.calistigi_sirket_id and personel.calistigi_sirket_id != gonderim.isyeri_id:
                        hatali_personeller.append(
                            f"{personel.ad} {personel.soyad} (Başka bir iş yerinde çalışıyor)")
                        continue

                    gonderim.personeller.append(personel)
                    personel.calistigi_sirket_id = form.isyeri.data
                    db.session.add(personel)

                    # Log kaydı
                    log = GonderimLog(
                        gonderim_id=gonderim.id,
                        personel_id=personel.id,
                        islem_tipi='eklendi',
                        tarih=datetime.utcnow()
                    )
                    db.session.add(log)
                    print(f"DEBUG - Personel eklendi: ID={personel.id}")

            # Gönderim bilgilerini güncelle
            gonderim.isyeri_id = form.isyeri.data
            gonderim.arac_id = form.arac.data

            # Şoför güncelleme
            if gonderim.arac:
                eski_sofor = gonderim.arac.sofor
                yeni_sofor_id = form.sofor.data

                # Eski şoförü çıkar
                if eski_sofor and eski_sofor.id != yeni_sofor_id:
                    if eski_sofor in gonderim.personeller:
                        gonderim.personeller.remove(eski_sofor)
                        eski_sofor.calistigi_sirket_id = None
                        db.session.add(eski_sofor)
                        print(
                            f"DEBUG - Eski şoför çıkarıldı: ID={eski_sofor.id}")

                # Yeni şoförü ekle
                if yeni_sofor_id != 0:
                    yeni_sofor = Personel.query.get(yeni_sofor_id)
                    if yeni_sofor:
                        if yeni_sofor.aktif_izin:
                            hatali_personeller.append(f"Şoför {yeni_sofor.ad} {
                                                      yeni_sofor.soyad} (İzinde)")
                        else:
                            gonderim.arac.sofor_id = yeni_sofor_id
                            if yeni_sofor not in gonderim.personeller:
                                gonderim.personeller.append(yeni_sofor)
                                yeni_sofor.calistigi_sirket_id = form.isyeri.data
                                db.session.add(yeni_sofor)
                                print(
                                    f"DEBUG - Yeni şoför eklendi: ID={yeni_sofor.id}")

            db.session.commit()
            flash('Gönderim başarıyla güncellendi.', 'success')

            if hatali_personeller:
                flash(f'Bazı personeller eklenemedi: {
                      ", ".join(hatali_personeller)}', 'warning')

            return redirect(url_for('index'))

        except Exception as e:
            db.session.rollback()
            print(f"DEBUG - Hata oluştu: {str(e)}")
            flash(f'Gönderim güncellenirken bir hata oluştu: {e}', 'danger')
            return redirect(request.url)

    logs = GonderimLog.query.filter_by(
        gonderim_id=gonderim_id).order_by(GonderimLog.tarih.desc()).all()

    return render_template('edit_gonderim.html',
                           form=form,
                           gonderim=gonderim,
                           logs=logs)


@app.route('/update_penalty/<int:vehicle_id>', methods=['POST'])
@login_required
@log_action(lambda current_user, **kwargs: f"Araç Ceza Güncelleme: Araç ID {kwargs.get('vehicle_id', 0)}")
def update_penalty(vehicle_id):
    if current_user.role not in ['admin', 'manager']:
        flash('Bu sayfaya erişim yetkiniz yok.', 'danger')
        return redirect(url_for('index'))

    vehicle = Vehicle.query.get(vehicle_id)
    if not vehicle:
        flash('Araç bulunamadı.')
        return redirect(url_for('index'))

    action = request.form.get('action')

    if action == 'mark_paid':
        if vehicle.penalty_status:
            vehicle.penalty_status = False
            vehicle.penalty_info = None
            log_entry = VehicleLog(
                vehicle_id=vehicle.id,
                change_type='penalty_update',
                old_value='Ceza Var',
                new_value='Ceza Yok'
            )
            db.session.add(log_entry)
            flash('Ceza başarıyla ödendi ve kaldırıldı.', 'success')
        else:
            flash('Araçta ceza bulunmamaktadır.', 'warning')

    elif action == 'no_penalty':
        if vehicle.penalty_status:
            vehicle.penalty_status = False
            vehicle.penalty_info = None
            log_entry = VehicleLog(
                vehicle_id=vehicle.id,
                change_type='penalty_update',
                old_value='Ceza Var',
                new_value='Ceza Yok'
            )
            db.session.add(log_entry)
            flash('Araçtan ceza kaldırıldı.', 'success')
        else:
            flash('Araçta ceza bulunmamaktadır.', 'warning')

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f'Ceza güncellenirken bir hata oluştu: {e}', 'danger')

    return redirect(url_for('edit_vehicle', vehicle_id=vehicle_id))

# Muhasebe


@app.route('/muhasebe', methods=['GET', 'POST'])
@login_required
@log_action(lambda current_user, **kwargs: "Muhasebe sayfası erişildi")
def muhasebe():
    if current_user.role not in ['admin', 'manager']:
        flash('Bu sayfaya erişim yetkiniz yok.', 'danger')
        return redirect(url_for('index'))

    # Sekme bilgisi
    tab = request.args.get('tab', 'gelirler')

    # Gelirler
    incomes = Income.query.order_by(Income.date.desc()).all()
    # Giderler
    expenses = Expense.query.order_by(Expense.date.desc()).all()
    # Maaşlar
    salaries = Salary.query.order_by(Salary.date.desc()).all()

    # Toplamlar
    total_income = sum(income.amount for income in incomes)
    total_expense = sum(expense.amount for expense in expenses) + \
        sum(salary.net_gelir for salary in salaries)
    net_balance = total_income - total_expense

    personel_listesi = Personel.query.all()
    isyeri_listesi = Isyeri.query.all()

    # **Formu Oluşturun ve Şablona Aktarın**
    form = MaasHesaplamaForm()
    form.personel_id.choices = [
        (p.id, f"{p.ad} {p.soyad}") for p in personel_listesi]

    return render_template('muhasebe.html', incomes=incomes, expenses=expenses, salaries=salaries,
                           total_income=total_income, total_expense=total_expense, net_balance=net_balance,
                           personel_listesi=personel_listesi, isyeri_listesi=isyeri_listesi, tab=tab, form=form)

# Gelir ekleme


@app.route('/gelir_ekle', methods=['POST'])
@login_required
@log_action(lambda current_user, **kwargs: f"Gelir Ekleme: €{request.form.get('amount')} - {request.form.get('category')}")
def gelir_ekle():
    if current_user.role not in ['admin', 'manager']:
        flash('Bu işlemi yapma yetkiniz yok.', 'danger')
        return redirect(url_for('muhasebe', tab='gelirler'))

    isyeri_id = request.form.get('isyeri_id')
    amount = request.form.get('amount', type=float)
    date_str = request.form.get('date')
    description = request.form.get('description')

    # Tarih Dönüştürme
    try:
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        flash('Geçersiz tarih formatı.', 'danger')
        return redirect(url_for('muhasebe', tab='gelirler'))

    new_income = Income(isyeri_id=isyeri_id, amount=amount,
                        date=date, description=description)
    db.session.add(new_income)
    db.session.commit()

    flash('Gelir başarıyla eklendi.', 'success')
    # Loglama decorator'ı sayesinde otomatik log oluşturulur
    return redirect(url_for('muhasebe', tab='gelirler'))

# Gider ekleme


@app.route('/gider_ekle', methods=['POST'])
@login_required
@log_action(lambda current_user, **kwargs: f"Gider Ekleme: €{request.form.get('amount')} - {request.form.get('category')}")
def gider_ekle():
    if current_user.role not in ['admin', 'manager']:
        flash('Bu işlemi yapma yetkiniz yok.', 'danger')
        return redirect(url_for('muhasebe', tab='giderler'))

    category = request.form.get('category')
    amount = request.form.get('amount', type=float)
    date_str = request.form.get('date')
    description = request.form.get('description')

    # Tarih Dönüştürme
    try:
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        flash('Geçersiz tarih formatı.', 'danger')
        return redirect(url_for('muhasebe', tab='giderler'))

    new_expense = Expense(category=category, amount=amount,
                          date=date, description=description)
    db.session.add(new_expense)
    db.session.commit()

    flash('Gider başarıyla eklendi.', 'success')
    # Loglama decorator'ı sayesinde otomatik log oluşturulur
    return redirect(url_for('muhasebe', tab='giderler'))


@app.route('/maas_hesapla', methods=['GET', 'POST'])
@login_required
@log_action(lambda current_user, **kwargs: "Maaş Hesaplama İşlemi")
def maas_hesapla():
    if current_user.role not in ['admin', 'manager']:
        flash('Bu işlemi yapma yetkiniz yok.', 'danger')
        return redirect(url_for('muhasebe', tab='maaslar'))

    form = MaasHesaplamaForm()
    form.personel_id.choices = [
        (p.id, f"{p.ad} {p.soyad}") for p in Personel.query.all()]

    if form.validate_on_submit():
        personel = Personel.query.get(form.personel_id.data)
        brut_saatlik_ucret = form.brut_saatlik_ucret.data
        standart_saatler = form.standart_saatler.data
        hafta_sonu_saatleri = form.hafta_sonu_saatleri.data
        gece_saatleri = form.gece_saatleri.data
        tatil_saatleri = form.tatil_saatleri.data
        date = form.date.data

        # Ek Ödeme Çarpanları
        hafta_sonu_carpani = 1.5  # %50 ek ödeme
        gece_carpani = 1.25       # %25 ek ödeme
        tatil_carpani = 2.0       # %100 ek ödeme

        # Brüt Ücret Hesaplaması
        standart_brut = standart_saatler * brut_saatlik_ucret
        hafta_sonu_brut = hafta_sonu_saatleri * brut_saatlik_ucret * hafta_sonu_carpani
        gece_brut = gece_saatleri * brut_saatlik_ucret * gece_carpani
        tatil_brut = tatil_saatleri * brut_saatlik_ucret * tatil_carpani

        toplam_brut = standart_brut + hafta_sonu_brut + gece_brut + tatil_brut

        # Vergi ve Kesintiler
        vergi_orani = 0.37  # Örnek vergi oranı
        sosyal_guvenlik_orani = 0.15  # Örnek sosyal güvenlik oranı

        gelir_vergisi = toplam_brut * vergi_orani
        sosyal_guvenlik = toplam_brut * sosyal_guvenlik_orani
        toplam_kesintiler = gelir_vergisi + sosyal_guvenlik

        net_gelir = toplam_brut - toplam_kesintiler

        # Salary modeline kaydetme
        new_salary = Salary(
            personel_id=personel.id,
            brut_saatlik_ucret=brut_saatlik_ucret,
            standart_saatler=standart_saatler,
            hafta_sonu_saatleri=hafta_sonu_saatleri,
            gece_saatleri=gece_saatleri,
            tatil_saatleri=tatil_saatleri,
            toplam_brut=toplam_brut,
            gelir_vergisi=gelir_vergisi,
            sosyal_guvenlik=sosyal_guvenlik,
            toplam_kesintiler=toplam_kesintiler,
            net_gelir=net_gelir,
            date=date
        )
        db.session.add(new_salary)
        db.session.commit()

        flash('Maaş başarıyla hesaplandı ve kaydedildi.', 'success')

        return redirect(url_for('muhasebe', tab='maaslar'))

    return render_template('muhasebe.html', form=form, tab='maaslar')


@app.route('/bordro_indir/<int:salary_id>', methods=['POST'])
@login_required
def bordro_indir(salary_id):
    if current_user.role not in ['admin', 'manager']:
        flash('Bu işlemi yapma yetkiniz yok.', 'danger')
        return redirect(url_for('muhasebe', tab='maaslar'))

    salary = Salary.query.get(salary_id)
    if not salary:
        flash('Bordro bulunamadı.')
        return redirect(url_for('muhasebe', tab='maaslar'))

    # Bordro HTML'ini render et
    rendered = render_template('bordro_pdf.html', salary=salary)

    # HTML'i PDF'e dönüştür
    pdf = HTML(string=rendered).write_pdf()

    # PDF'i indirme olarak döndür
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=bordro_{
        salary.personel.ad}_{salary.personel.soyad}_{salary.date}.pdf'

    return response

# Yönetici Paneli


# Yönetici Paneli

@app.route('/yonetici_paneli', methods=['GET', 'POST'])
@login_required
@log_action(lambda current_user, **kwargs: "Yönetici Paneli Erişimi")
def yonetici_paneli():
    if current_user.role != 'admin':
        flash('Bu sayfaya erişim yetkiniz yok.', 'danger')
        return redirect(url_for('index'))

    # Formları ve verileri başlatma
    add_house_form = AddHouseForm()
    add_house_form.residents.choices = [
        (p.id, f"{p.ad} {p.soyad}") for p in Personel.query.all()]
    country_choices = get_country_choices()

    # Formu oluştur
    isyeri_form = EditIsyeriForm()

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add_isyeri':
            if isyeri_form.validate_on_submit():
                yeni_isyeri = Isyeri(
                    ad=isyeri_form.ad.data,
                    kvk_nummer=isyeri_form.kvk_nummer.data,
                    sahib=isyeri_form.sahib.data,
                    iletisim_telefon=isyeri_form.iletisim_telefon.data,
                    faaliyet_sektoru=isyeri_form.faaliyet_sektoru.data,
                    banka_hesabi=isyeri_form.banka_hesabi.data,
                    adres=isyeri_form.adres.data
                )
                try:
                    db.session.add(yeni_isyeri)
                    db.session.commit()
                    flash('İş yeri başarıyla eklendi.', 'success')
                    create_log(current_user.id, f"İş Yeri Ekleme: {
                               yeni_isyeri.ad}")
                except Exception as e:
                    db.session.rollback()
                    flash(f'İş yeri eklenirken bir hata oluştu: {e}', 'danger')
            else:
                flash(
                    'İş yeri ekleme formunda hatalar var. Lütfen kontrol edin.', 'danger')

            return redirect(url_for('yonetici_paneli', tab='isyeri'))

        elif action == 'remove_isyeri':
            # İş Yeri Silme İşlemi
            isyeri_id = request.form.get('isyeri_id')
            if isyeri_id:
                isyeri = Isyeri.query.get(isyeri_id)
                if isyeri:
                    try:
                        db.session.delete(isyeri)
                        db.session.commit()
                        flash('İş yeri başarıyla silindi.', 'success')
                        create_log(current_user.id,
                                   f"İş Yeri Silme: {isyeri.ad}")
                    except Exception as e:
                        db.session.rollback()
                        flash(f'İş yeri silinirken bir hata oluştu: {
                              e}', 'danger')
                else:
                    flash('Silinecek iş yeri bulunamadı.', 'danger')
            return redirect(url_for('yonetici_paneli', tab='isyeri'))

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add_personel':
            personel_ad = request.form.get('personel_ad')
            personel_soyad = request.form.get('personel_soyad')
            personel_email = request.form.get('personel_email')
            personel_telefon = request.form.get('personel_telefon')
            personel_adres = request.form.get('personel_adres')
            personel_banka_hesabi = request.form.get('personel_banka_hesabi')
            calistigi_sirket = request.form.get('calistigi_sirket')
            personel_bilgi = request.form.get('personel_bilgi')
            personel_bsn_numarasi = request.form.get('personel_bsn_numarasi')
            personel_uyruk = request.form.get('personel_uyruk')
            calistigi_sirket_id = None

            if calistigi_sirket and calistigi_sirket.strip():
                try:
                    calistigi_sirket_id = int(calistigi_sirket)
                    if Isyeri.query.get(calistigi_sirket_id):
                        personel_status = 'Aktif'
                    else:
                        calistigi_sirket_id = None
                        personel_status = 'Pasif'
                except (ValueError, TypeError):
                    calistigi_sirket_id = None
                    personel_status = 'Pasif'
            else:
                calistigi_sirket_id = None
                personel_status = 'Pasif'

            foto = request.files.get('personel_foto')
            kimlik_foto = request.files.get('personel_kimlik_foto')
            foto_path = None
            kimlik_foto_path = None

            if foto and allowed_file(foto.filename):
                filename = secure_filename(foto.filename)
                extension = os.path.splitext(filename)[1]
                foto_filename = f"{uuid.uuid4()}{extension}"
                foto_path_full = os.path.join(
                    app.config['UPLOAD_FOLDER'], foto_filename)
                foto.save(foto_path_full)
                foto_path = os.path.join('static/uploads', foto_filename)

            if kimlik_foto and allowed_file(kimlik_foto.filename):
                filename = secure_filename(kimlik_foto.filename)
                extension = os.path.splitext(filename)[1]
                kimlik_foto_filename = f"{uuid.uuid4()}{extension}"
                kimlik_foto_path_full = os.path.join(
                    app.config['UPLOAD_FOLDER'], kimlik_foto_filename)
                kimlik_foto.save(kimlik_foto_path_full)
                kimlik_foto_path = os.path.join(
                    'static/uploads', kimlik_foto_filename)

            yeni_personel = Personel(
                ad=personel_ad,
                soyad=personel_soyad,
                email=personel_email,
                telefon=personel_telefon,
                adres=personel_adres,
                banka_hesabi=personel_banka_hesabi,
                calistigi_sirket_id=calistigi_sirket_id,
                bilgi=personel_bilgi,
                bsn_numarasi=personel_bsn_numarasi,
                foto_path=foto_path,
                kimlik_foto_path=kimlik_foto_path,
                uyruk=personel_uyruk,
                status=personel_status
            )

            try:
                db.session.add(yeni_personel)
                db.session.commit()

                # Eğer personel bir iş yerine atanmış ise ve orada bir gonderim var ise log ekle
                if calistigi_sirket_id:
                    gonderim = Gonderim.query.filter_by(
                        isyeri_id=calistigi_sirket_id).first()
                    if gonderim:
                        log = GonderimLog(
                            gonderim_id=gonderim.id,
                            personel_id=yeni_personel.id,
                            islem_tipi='eklendi'
                        )
                        db.session.add(log)
                        db.session.commit()

                flash('Personel başarıyla eklendi.', 'success')
                create_log(current_user.id, f"Personel Ekleme: {
                           personel_ad} {personel_soyad}")
            except Exception as e:
                db.session.rollback()
                flash(f'Personel eklenirken bir hata oluştu: {e}', 'danger')

            return redirect(url_for('yonetici_paneli', tab='personel'))

        elif action == 'remove_personel':
            # Personel Silme İşlemi
            personel_id = request.form.get('personel_id')
            if personel_id:
                personel = Personel.query.get(personel_id)
                if personel:
                    try:
                        # Fotoğrafları silme
                        if personel.foto_path:
                            foto_full_path = os.path.join(
                                app.root_path, personel.foto_path)
                            if os.path.exists(foto_full_path):
                                os.remove(foto_full_path)
                        if personel.kimlik_foto_path:
                            kimlik_foto_full_path = os.path.join(
                                app.root_path, personel.kimlik_foto_path)
                            if os.path.exists(kimlik_foto_full_path):
                                os.remove(kimlik_foto_full_path)

                        db.session.delete(personel)
                        db.session.commit()
                        flash('Personel başarıyla silindi.', 'success')
                        create_log(current_user.id, f"Personel Silme: {
                                   personel.ad} {personel.soyad}")
                    except Exception as e:
                        db.session.rollback()
                        flash(f'Personel silinirken bir hata oluştu: {
                              e}', 'danger')
                else:
                    flash('Silinecek personel bulunamadı.', 'danger')
            return redirect(url_for('yonetici_paneli', tab='personel'))

        elif action == 'add_vehicle':
            # Araç Ekleme İşlemi
            arac_plaka = request.form.get('arac_plaka')
            arac_marka_model = request.form.get('arac_marka_model')
            sofor_id = request.form.get('sofor')
            km = request.form.get('km', type=int)
            parking_zone = request.form.get('parking_zone')
            maintenance_status = request.form.get('maintenance_status') == 'on'
            penalty_status = request.form.get('penalty_status') == 'on'
            penalty_info = request.form.get(
                'penalty_info') if penalty_status else None

            # Ruhsat Fotoğrafı Yükleme
            ruhsat_foto = request.files.get('ruhsat_foto')
            ruhsat_photo_path = None

            if ruhsat_foto and allowed_file(ruhsat_foto.filename):
                filename = secure_filename(ruhsat_foto.filename)
                extension = os.path.splitext(filename)[1]
                ruhsat_foto_filename = f"{uuid.uuid4()}{extension}"
                ruhsat_photo_path = os.path.join(
                    app.config['UPLOAD_FOLDER'], ruhsat_foto_filename)
                try:
                    ruhsat_foto.save(ruhsat_photo_path)
                    ruhsat_photo_path = os.path.join(
                        'static/uploads', ruhsat_foto_filename)
                except Exception as e:
                    flash(f'Ruhsat fotoğrafı yüklenirken bir hata oluştu: {
                          e}', 'danger')
                    return redirect(url_for('yonetici_paneli', tab='arac-ekle'))

            if not arac_plaka or not arac_marka_model or not sofor_id:
                flash('Lütfen gerekli tüm alanları doldurun.', 'danger')
                return redirect(url_for('yonetici_paneli', tab='arac-ekle'))

            sofor = Personel.query.get(sofor_id)
            if not sofor:
                flash('Seçilen şoför bulunamadı.', 'danger')
                return redirect(url_for('yonetici_paneli', tab='arac-ekle'))

            yeni_arac = Vehicle(
                arac_plaka=arac_plaka,
                arac_marka_model=arac_marka_model,
                sofor_id=sofor.id,
                km=km or 0,
                previous_km=km or 0,
                maintenance_status=maintenance_status,
                parking_zone=parking_zone,
                ruhsat_photo_path=ruhsat_photo_path,
                penalty_status=penalty_status,
                penalty_info=penalty_info
            )

            try:
                db.session.add(yeni_arac)
                db.session.commit()
                flash('Araç başarıyla eklendi.', 'success')
                create_log(current_user.id, f"Araç Ekleme: {
                           arac_plaka} - {arac_marka_model}")
            except Exception as e:
                db.session.rollback()
                flash(f'Araç eklenirken bir hata oluştu: {e}', 'danger')

            return redirect(url_for('yonetici_paneli', tab='arac-ekle'))

        elif action == 'remove_vehicle':
            # Araç Silme İşlemi
            vehicle_id = request.form.get('vehicle_id')
            arac = Vehicle.query.get(vehicle_id)
            if arac:
                try:
                    # Ruhsat fotoğrafını silme
                    if arac.ruhsat_photo_path:
                        ruhsat_full_path = os.path.join(
                            app.root_path, arac.ruhsat_photo_path)
                        if os.path.exists(ruhsat_full_path):
                            os.remove(ruhsat_full_path)

                    db.session.delete(arac)
                    db.session.commit()
                    flash('Araç başarıyla silindi.', 'success')
                    create_log(current_user.id, f"Araç Silme: {
                               arac.arac_plaka} - {arac.arac_marka_model}")
                except Exception as e:
                    db.session.rollback()
                    flash(f'Araç silinirken bir hata oluştu: {e}', 'danger')
            else:
                flash('Silinecek araç bulunamadı.', 'danger')
            return redirect(url_for('yonetici_paneli', tab='arac-ekle'))

        elif action == 'add_house':
            # Ev Ekleme İşlemi
            form = AddHouseForm(request.form)
            form.residents.choices = add_house_form.residents.choices
            form.photos.data = request.files.getlist('photos')
            form.insurance_documents.data = request.files.getlist(
                'insurance_documents')

            if form.validate_on_submit():
                # Kapasite kontrolü
                selected_residents = form.residents.data
                if len(selected_residents) > form.capacity.data:
                    flash('Seçilen personel sayısı kapasiteden fazla olamaz.', 'danger')
                    return redirect(url_for('yonetici_paneli', tab='ev'))

                # Yeni evi oluşturma
                new_house = House(
                    address=form.address.data,
                    capacity=form.capacity.data,
                    insurance_status=form.insurance_status.data,
                    insurance_date=form.insurance_date.data,
                    vve_meeting_date=form.vve_meeting_date.data
                )

                # Fotoğrafları kaydetme
                photo_paths = []
                for photo in form.photos.data:
                    if photo and allowed_file(photo.filename):
                        filename = secure_filename(photo.filename)
                        extension = os.path.splitext(filename)[1]
                        photo_filename = f"{uuid.uuid4()}{extension}"
                        photo_path = os.path.join(
                            app.config['UPLOAD_FOLDER'], photo_filename)
                        photo.save(photo_path)
                        photo_paths.append(os.path.join(
                            'static/uploads', photo_filename))
                new_house.photos = json.dumps(photo_paths)

                # Sigorta belgelerini kaydetme
                document_paths = []
                for document in form.insurance_documents.data:
                    if document and allowed_file(document.filename, {'pdf'}):
                        filename = secure_filename(document.filename)
                        extension = os.path.splitext(filename)[1]
                        document_filename = f"{uuid.uuid4()}{extension}"
                        document_path = os.path.join(
                            app.config['UPLOAD_FOLDER'], document_filename)
                        document.save(document_path)
                        document_paths.append(os.path.join(
                            'static/uploads', document_filename))
                new_house.insurance_documents = json.dumps(document_paths)

                # Evde yaşayan personelleri ekleme
                for personel_id in selected_residents:
                    personel = Personel.query.get(personel_id)
                    if personel:
                        house_personel = HousePersonel(
                            personel=personel, house=new_house)
                        db.session.add(house_personel)

                try:
                    db.session.add(new_house)
                    db.session.commit()
                    flash('Ev başarıyla eklendi.', 'success')
                    create_log(current_user.id, f"Ev Ekleme: {
                               new_house.address}")
                    return redirect(url_for('yonetici_paneli', tab='ev'))
                except Exception as e:
                    db.session.rollback()
                    flash(f'Ev eklenirken bir hata oluştu: {e}', 'danger')
                    return redirect(url_for('yonetici_paneli', tab='ev'))
            else:
                flash(
                    'Form doğrulama hatası: Lütfen tüm alanları kontrol edin.', 'danger')
                # Form hatalarıyla birlikte sayfayı yeniden yükleyeceğiz

        elif action == 'remove_house':
            # Ev Silme İşlemi
            house_id = request.form.get('house_id')
            if house_id:
                house = db.session.get(House, house_id)
                if house:
                    try:
                        # Fotoğrafları ve belgeleri silme
                        for photo_path in house.photos_list:
                            full_path = os.path.join(app.root_path, photo_path)
                            if os.path.exists(full_path):
                                os.remove(full_path)
                        for doc_path in house.insurance_documents_list:
                            full_path = os.path.join(app.root_path, doc_path)
                            if os.path.exists(full_path):
                                os.remove(full_path)
                        db.session.delete(house)
                        db.session.commit()
                        flash('Ev başarıyla silindi.', 'success')
                        create_log(current_user.id,
                                   f"Ev Silme: {house.address}")
                    except Exception as e:
                        db.session.rollback()
                        flash(f'Ev silinirken bir hata oluştu: {e}', 'danger')
                else:
                    flash('Silinecek ev bulunamadı.', 'danger')
            return redirect(url_for('yonetici_paneli', tab='ev'))

        else:
            flash('Geçersiz işlem.', 'danger')
            return redirect(url_for('yonetici_paneli'))

    # GET isteği veya POST isteğinden sonra sayfayı render etme
    personel_listesi = Personel.query.all()
    isyeri_listesi = Isyeri.query.all()
    vehicle_listesi = Vehicle.query.all()
    house_listesi = House.query.all()

    return render_template('yonetici_paneli.html',
                           isyeri_listesi=isyeri_listesi,
                           isyeri_form=isyeri_form,
                           personel_listesi=personel_listesi,
                           vehicle_listesi=vehicle_listesi,
                           house_listesi=house_listesi,
                           add_house_form=add_house_form,
                           country_choices=country_choices)

# Personel Gönderme


@app.route('/gonder', methods=['GET', 'POST'])
@login_required
@log_action(lambda current_user, **kwargs: "Personel Gönderme İşlemi")
def gonder():
    if current_user.role not in ['admin', 'manager']:
        flash('Bu sayfaya erişim yetkiniz yok.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        isyeri_id = request.form.get('isyeri')
        arac_id = request.form.get('arac')
        personel_ids = request.form.getlist('personeller[]')
        tarih = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Debug için gelen verileri kontrol et
        print(f"DEBUG - Gelen veriler:")
        print(f"İşyeri ID: {isyeri_id}")
        print(f"Araç ID: {arac_id}")
        print(f"Seçilen Personeller: {personel_ids}")

        # İşyeri kontrolü
        if not isyeri_id:
            flash('Lütfen bir iş yeri seçin.', 'warning')
            return redirect(url_for('gonder'))

        # Personel seçim kontrolü
        if not personel_ids:
            flash('Lütfen en az bir personel seçin.', 'warning')
            return redirect(url_for('gonder'))

        # Aynı iş yerine daha önce gönderim yapıldı mı kontrolü
        mevcut_gonderim = Gonderim.query.filter_by(isyeri_id=isyeri_id).first()
        if mevcut_gonderim:
            flash(
                'Bu iş yerine zaten bir gönderim yapıldı. Aynı iş yerine ikinci gönderim yapılamaz.', 'warning')
            return redirect(url_for('gonder'))

        try:
            # Gönderimi oluştur
            new_gonderim = Gonderim(
                isyeri_id=isyeri_id,
                arac_id=arac_id,
                tarih=tarih
            )
            db.session.add(new_gonderim)
            db.session.flush()  # ID'nin oluşması için flush yapıyoruz

            basarili_personeller = []  # Başarılı eklenen personeller
            hatali_personeller = []    # Eklenemeyen personeller

            # Seçilen personelleri işle
            for personel_id in personel_ids:
                personel = Personel.query.get(personel_id)
                if personel:
                    # İzin kontrolü
                    if personel.aktif_izin:
                        hatali_personeller.append(
                            f"{personel.ad} {personel.soyad} (İzinde)")
                        print(
                            f"DEBUG - Personel izinde: {personel.ad} {personel.soyad}")
                        continue

                    if personel.calistigi_sirket_id is not None:
                        mevcut_gonderim = Gonderim.query.filter_by(
                            isyeri_id=personel.calistigi_sirket_id).first()

                        if mevcut_gonderim and personel in mevcut_gonderim.personeller:
                            hatali_personeller.append(f"{personel.ad} {
                                                      personel.soyad} (Başka bir iş yerinde çalışıyor: {mevcut_gonderim.isyeri.ad})")
                        continue
                    try:
                        # Önce personelin iş yeri bilgisini güncelle
                        personel.calistigi_sirket_id = int(isyeri_id)
                        db.session.add(personel)
                        print(f"DEBUG - Personel güncellendi: ID={
                              personel.id}, Şirket ID={personel.calistigi_sirket_id}")

                        # Sonra gönderime ekle
                        if personel not in new_gonderim.personeller:
                            new_gonderim.personeller.append(personel)
                            basarili_personeller.append(personel)

                            # Log kaydı
                            log = GonderimLog(
                                gonderim_id=new_gonderim.id,
                                personel_id=personel.id,
                                islem_tipi='eklendi',
                                tarih=datetime.utcnow()
                            )
                            db.session.add(log)
                            print(
                                f"DEBUG - Log kaydı oluşturuldu: Personel={personel.id}")

                    except Exception as e:
                        hatali_personeller.append(
                            f"{personel.ad} {personel.soyad} (Hata: {str(e)})")
                        print(f"DEBUG - Personel eklenirken hata: {str(e)}")
                        continue

            # Şoför işlemleri
            if arac_id:
                arac = Vehicle.query.get(arac_id)
                if arac and arac.sofor_id:
                    sofor = Personel.query.get(arac.sofor_id)
                    if sofor:
                        if sofor.aktif_izin:
                            hatali_personeller.append(
                                f"Şoför {sofor.ad} {sofor.soyad} (İzinde)")
                            print(
                                f"DEBUG - Şoför izinde: {sofor.ad} {sofor.soyad}")
                        else:
                            if sofor not in new_gonderim.personeller:
                                # Şoförün iş yeri bilgisini güncelle
                                sofor.calistigi_sirket_id = int(isyeri_id)
                                db.session.add(sofor)
                                print(
                                    f"DEBUG - Şoför güncellendi: ID={sofor.id}, Şirket ID={sofor.calistigi_sirket_id}")

                                # Gönderime ekle
                                new_gonderim.personeller.append(sofor)
                                basarili_personeller.append(sofor)

                                # Şoför log kaydı
                                log = GonderimLog(
                                    gonderim_id=new_gonderim.id,
                                    personel_id=sofor.id,
                                    islem_tipi='eklendi',
                                    tarih=datetime.utcnow()
                                )
                                db.session.add(log)
                                print(f"DEBUG - Şoför log kaydı oluşturuldu")

            # Değişiklikleri kaydet
            db.session.commit()
            print("DEBUG - Tüm değişiklikler kaydedildi")

            # Sonuç mesajları
            if basarili_personeller:
                flash(f'{len(basarili_personeller)
                         } personel başarıyla gönderildi.', 'success')
                create_log(current_user.id, f"Personel Gönderme: {
                           len(basarili_personeller)} kişi, İş Yeri ID: {isyeri_id}, Araç ID: {arac_id}")

            if hatali_personeller:
                flash(f'Bazı personeller eklenemedi: {
                      ", ".join(hatali_personeller)}', 'warning')

            return redirect(url_for('index'))

        except Exception as e:
            db.session.rollback()
            print(f"DEBUG - Genel hata: {str(e)}")
            flash(f'Gönderim sırasında bir hata oluştu: {e}', 'danger')
            return redirect(url_for('gonder'))

    # GET isteği için form sayfasını hazırla
    try:
        # Tüm araçların şoför ID'lerini al
        sofor_ids = db.session.query(Vehicle.sofor_id).filter(
            Vehicle.sofor_id.isnot(None)).all()
        # Liste içindeki tuple'lardan ID'leri çıkar
        sofor_ids = [id[0] for id in sofor_ids]

        # Sadece çalışmayan ve izinde olmayan personelleri getir
        personel_listesi = Personel.query.filter(
            (Personel.calistigi_sirket_id.is_(None)) &  # Çalışmayan
            ~Personel.id.in_(                           # İzinde olmayan
                db.session.query(IzinKaydi.personel_id)
                .filter(IzinKaydi.durum == 'aktif')
            ) &
            ~Personel.id.in_(sofor_ids)                # Şoför olmayan
        ).all()

        vehicle_listesi = Vehicle.query.all()
        isyeri_listesi = Isyeri.query.all()

        return render_template('gonder.html',
                               personel_listesi=personel_listesi,
                               vehicle_listesi=vehicle_listesi,
                               isyeri_listesi=isyeri_listesi)
    except Exception as e:
        print(f"DEBUG - Form hazırlanırken hata: {str(e)}")
        flash('Form hazırlanırken bir hata oluştu.', 'danger')
        return redirect(url_for('index'))


# Personel Düzenleme


def get_izin_ayarlari():
    """Mevcut izin ayarlarını döndürür"""
    return IzinAyarlari.query.first()


@app.route('/edit_personel/<int:personel_id>', methods=['GET', 'POST'])
@login_required
@log_action(lambda current_user, **kwargs: f"Personel Düzenleme: Personel ID {kwargs.get('personel_id', 0)}")
def edit_personel(personel_id):
    personel = Personel.query.get(personel_id)
    if not personel:
        flash('Personel bulunamadı.')
        return redirect(url_for('index'))

    form = EditPersonelForm(obj=personel)
    izin_form = IzinEkleForm()  # İzin formunu ekleyin
    form.uyruk.choices = get_country_choices()
    form.calistigi_sirket_id.choices = [
        (0, 'İş Yeri Seçiniz')] + [(i.id, i.ad) for i in Isyeri.query.all()]
    # Ev seçenekleri
    houses = House.query.all()
    form.house_id.choices = [(0, 'Ev Seçiniz')] + \
        [(h.id, h.address) for h in houses]

    if request.method == 'GET':
        # Personelin mevcut verileri form alanlarına aktarılır.
        form.calistigi_sirket_id.data = personel.calistigi_sirket_id if personel.calistigi_sirket_id else 0

        # Personelin kaldığı evi ve kira bilgisini HousePersonel tablosundan bulma
        house_personel = HousePersonel.query.filter_by(
            personel_id=personel.id).first()
        if house_personel:
            form.house_id.data = house_personel.house_id
            form.weekly_rent.data = house_personel.weekly_rent
        else:
            form.house_id.data = 0
            form.weekly_rent.data = 0

    if form.validate_on_submit():
        # Temel personel bilgilerini güncelle
        personel.ad = form.ad.data
        personel.soyad = form.soyad.data
        personel.email = form.email.data
        personel.telefon = form.telefon.data
        personel.adres = form.adres.data
        personel.banka_hesabi = form.banka_hesabi.data
        personel.bilgi = form.bilgi.data
        personel.bsn_numarasi = form.bsn_numarasi.data
        personel.uyruk = form.uyruk.data
        personel.status = form.status.data

        if form.calistigi_sirket_id.data == 0:
            personel.calistigi_sirket_id = None
        else:
            personel.calistigi_sirket_id = form.calistigi_sirket_id.data

        update_personel_status(personel)

        # BSN Numarası kontrolü
        if personel.bsn_numarasi and not is_valid_bsn(personel.bsn_numarasi):
            flash('BSN Numarası 9 haneli rakamlardan oluşmalıdır.')
            return redirect(request.url)

        # Fotoğraf işlemleri
        foto = form.foto_path.data
        kimlik_foto = form.kimlik_foto_path.data

        if foto and foto.filename:
            if allowed_file(foto.filename):
                filename = secure_filename(foto.filename)
                extension = os.path.splitext(filename)[1]
                foto_filename = f"{uuid.uuid4()}{extension}"
                foto_path_abs = os.path.join(
                    app.config['UPLOAD_FOLDER'], foto_filename)
                foto.save(foto_path_abs)
                if personel.foto_path:
                    old_foto_path = os.path.join(
                        app.root_path, personel.foto_path)
                    if os.path.exists(old_foto_path):
                        os.remove(old_foto_path)
                personel.foto_path = os.path.join(
                    'static/uploads', foto_filename)
            else:
                flash(
                    'Geçersiz dosya türü. Sadece png, jpg, jpeg ve gif dosyalarına izin verilir.')
                return redirect(request.url)

        if kimlik_foto and kimlik_foto.filename:
            if allowed_file(kimlik_foto.filename):
                filename = secure_filename(kimlik_foto.filename)
                extension = os.path.splitext(filename)[1]
                kimlik_foto_filename = f"{uuid.uuid4()}{extension}"
                kimlik_foto_path_abs = os.path.join(
                    app.config['UPLOAD_FOLDER'], kimlik_foto_filename)
                kimlik_foto.save(kimlik_foto_path_abs)
                if personel.kimlik_foto_path:
                    old_kimlik_foto_path = os.path.join(
                        app.root_path, personel.kimlik_foto_path)
                    if os.path.exists(old_kimlik_foto_path):
                        os.remove(old_kimlik_foto_path)
                personel.kimlik_foto_path = os.path.join(
                    'static/uploads', kimlik_foto_filename)
            else:
                flash(
                    'Geçersiz dosya türü. Sadece png, jpg, jpeg ve gif dosyalarına izin verilir.')
                return redirect(request.url)

        # Ev ve kira bilgisini güncelle
        selected_house_id = form.house_id.data
        weekly_rent = form.weekly_rent.data or 0

        # Mevcut house_personel kaydını bul
        house_personel = HousePersonel.query.filter_by(
            personel_id=personel.id).first()

        if selected_house_id == 0:
            # Ev seçili değilse kaydı sil
            if house_personel:
                db.session.delete(house_personel)
        else:
            # Ev seçiliyse güncelle veya yeni kayıt oluştur
            if house_personel:
                house_personel.house_id = selected_house_id
                house_personel.weekly_rent = weekly_rent
            else:
                new_hp = HousePersonel(
                    house_id=selected_house_id,
                    personel_id=personel.id,
                    weekly_rent=weekly_rent
                )
                db.session.add(new_hp)

            # Mevcut haftanın kira ödemesini oluştur veya güncelle
            year, week_number = get_current_year_week()
            rent_payment = RentPayment.query.filter_by(
                house_id=selected_house_id,
                personel_id=personel.id,
                year=year,
                week_number=week_number
            ).first()

            if not rent_payment:
                rent_payment = RentPayment(
                    house_id=selected_house_id,
                    personel_id=personel.id,
                    year=year,
                    week_number=week_number,
                    weekly_rent=weekly_rent,
                    paid=False
                )
                db.session.add(rent_payment)
            else:
                rent_payment.weekly_rent = weekly_rent

        try:
            db.session.commit()
            flash('Personel bilgileri başarıyla güncellendi.')
            if selected_house_id != 0:
                return redirect(url_for('index'))
        except Exception as e:
            db.session.rollback()
            flash(f'Bir hata oluştu: {e}', 'danger')
            return redirect(request.url)

        if selected_house_id == 0:
            # Ev seçili değilse kaydı sil
            if house_personel:
                db.session.delete(house_personel)
        else:
            # Ev seçiliyse güncelle veya yeni kayıt oluştur
            if house_personel:
                house_personel.house_id = selected_house_id
                house_personel.weekly_rent = weekly_rent
            else:
                new_hp = HousePersonel(
                    house_id=selected_house_id,
                    personel_id=personel.id,
                    weekly_rent=weekly_rent
                )
                db.session.add(new_hp)

        try:
            db.session.commit()
            flash('Personel bilgileri başarıyla güncellendi.', 'success')
            return redirect(url_for('edit_personel', personel_id=personel.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Bir hata oluştu: {e}', 'danger')
            return redirect(url_for('edit_personel', personel_id=personel.id))

    # Form render edilirken detay kartlarını da gönder
    return render_template('edit_personel.html',
                           izin_form=izin_form,
                           form=form,
                           personel=personel,
                           # Fonksiyonu template'e gönder
                           calculate_working_duration=calculate_working_duration,
                           # Toplam saat hesaplama fonksiyonunu gönder
                           calculate_total_working_hours=calculate_total_working_hours,
                           get_izin_ayarlari=get_izin_ayarlari,
                           edit_personel_details=render_template('_personel_details.html',
                                                                 personel=personel,
                                                                 current_year=datetime.now().year,
                                                                 current_week=datetime.now().isocalendar()[1])
                           )

# İş Yeri Düzenleme


@app.route('/edit_isyeri/<int:isyeri_id>', methods=['GET', 'POST'])
@login_required
@log_action(lambda current_user, **kwargs: f"İş Yeri Düzenleme: İş Yeri ID {kwargs.get('isyeri_id', 0)}")
def edit_isyeri(isyeri_id):
    if current_user.role not in ['admin', 'manager']:
        flash('Bu sayfaya erişim yetkiniz yok.', 'danger')
        return redirect(url_for('index'))

    isyeri = db.session.get(Isyeri, isyeri_id)
    if not isyeri:
        flash('İş yeri bulunamadı.')
        return redirect(url_for('index'))

    form = EditIsyeriForm(obj=isyeri)

    if form.validate_on_submit():
        form.populate_obj(isyeri)
        try:
            db.session.commit()
            flash('İş yeri bilgileri başarıyla güncellendi.')
            return redirect(url_for('index'))
        except Exception as e:
            db.session.rollback()
            flash(f'Bir hata oluştu: {e}', 'danger')

    return render_template('edit_isyeri.html', form=form, isyeri=isyeri)

# Yönetici Ayarları


@app.route('/yonetici_ayarlari', methods=['GET', 'POST'])
@login_required
@log_action(lambda current_user, **kwargs: "Yönetici Ayarları Erişimi")
def yonetici_ayarlari():
    if current_user.role != 'admin':
        flash('Bu sayfaya erişim yetkiniz yok.', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add_user':
            username = request.form['username']
            password = request.form['password']
            role = request.form['role']
            hashed_password = generate_password_hash(password)
            new_user = User(username=username,
                            password=hashed_password, role=role)
            try:
                db.session.add(new_user)
                db.session.commit()
                flash('Kullanıcı başarıyla eklendi.')
                # Loglama
                create_log(current_user.id, f"Kullanıcı Ekleme: {
                           username} ({role})")
            except Exception as e:
                db.session.rollback()
                flash('Bu kullanıcı adı zaten mevcut.', 'danger')
            return redirect(url_for('yonetici_ayarlari'))
        elif action == 'remove_user':
            user_id = request.form.get('user_id')
            user = User.query.get(user_id)
            if user:
                if user.username == current_user.username:
                    flash('Kendinizi silemezsiniz.', 'danger')
                else:
                    db.session.delete(user)
                    db.session.commit()
                    flash('Kullanıcı başarıyla silindi.', 'success')
                    # Loglama
                    create_log(current_user.id,
                               f"Kullanıcı Silme: {user.username}")
            else:
                flash('Kullanıcı bulunamadı.', 'danger')
            return redirect(url_for('yonetici_ayarlari'))
    else:
        users = User.query.all()
        logs = Log.query.order_by(Log.timestamp.desc()).all()
        backups = sorted(os.listdir(BACKUP_DIR), reverse=True)

        # İzin ayarları formunu oluştur
    leave_settings_form = IzinAyarlariForm()

    # Mevcut ayarları form'a yükle
    settings = IzinAyarlari.query.first()
    if settings:
        leave_settings_form.saat_esigi.data = settings.saat_esigi
        leave_settings_form.ay_esigi.data = settings.ay_esigi
        leave_settings_form.izin_gun_sayisi.data = settings.izin_gun_sayisi

    return render_template(
        'yonetici_ayarlari.html',
        users=users,
        logs=logs,
        backups=backups,
        active_tab=request.args.get('tab'),
        leave_settings_form=leave_settings_form  # Form'u template'e gönder
    )

    # Yedek Dosyasını İndirme Rotası


@app.route('/admin/download_backup/<filename>')
@login_required
def download_backup(filename):
    if current_user.role != 'admin':
        flash("Bu sayfaya erişim yetkiniz yok.", "danger")
        return redirect(url_for('index'))
    return send_from_directory(BACKUP_DIR, filename, as_attachment=True)

# Yedek Dosyasını Silme Rotası


@app.route('/admin/delete_backup/<filename>', methods=['POST'])
@login_required
def delete_backup(filename):
    if current_user.role != 'admin':
        flash("Bu sayfaya erişim yetkiniz yok.", "danger")
        return redirect(url_for('index'))
    file_path = os.path.join(BACKUP_DIR, filename)
    if os.path.exists(file_path):
        os.remove(file_path)
        flash("Yedek dosyası silindi.", "success")
    else:
        flash("Dosya bulunamadı.", "danger")
    return redirect(url_for('yonetici_ayarlari'))

# Manuel Yedekleme Rotası


@app.route('/admin/backup_database', methods=['POST'])
@login_required
def manual_backup():
    if current_user.role != 'admin':
        flash("Bu işlemi yapma yetkiniz yok.", "danger")
        return redirect(url_for('index'))
    backup_database()
    flash("Veritabanı başarıyla yedeklendi.", "success")
    return redirect(url_for('yonetici_ayarlari'))

# Veritabanını Geri Yükleme Rotası


@app.route('/admin/restore_backup', methods=['POST'])
@login_required
def restore_backup():
    if current_user.role != 'admin':
        flash("Bu sayfaya erişim yetkiniz yok.", "danger")
        return redirect(url_for('index'))

    backup_file = request.files.get('backup_file')
    if backup_file and backup_file.filename.endswith('.db'):
        filename = secure_filename(backup_file.filename)
        file_path = os.path.join(BACKUP_DIR, filename)
        backup_file.save(file_path)

        # Mevcut Veritabanını Yedekleme (Geri Yükleme Öncesi)
        current_db = os.path.join(app.instance_path, 'personel_takip.db')
        restore_backup_path = f"{current_db}.restore_before_{
            datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy(current_db, restore_backup_path)

        try:
            shutil.copy(file_path, current_db)
            db.session.remove()
            db.engine.dispose()
            flash("Veritabanı başarıyla geri yüklendi.", "success")
        except Exception as e:
            flash(f"Geri yükleme sırasında hata oluştu: {e}", "danger")
    else:
        flash("Geçersiz dosya formatı. Lütfen .db dosyası yükleyin.", "danger")
    return redirect(url_for('yonetici_ayarlari'))

# Paylaşım Sayfası


@app.route('/share/<int:gonderim_id>')
def share_gonderim(gonderim_id):
    # Gönderim bilgisini veritabanından çek
    gonderim = Gonderim.query.get_or_404(gonderim_id)

    # İş yeri adresini al
    isyeri_adresi = gonderim.isyeri.adres if gonderim.isyeri else "Adres bilgisi mevcut değil."

    # İşe giden personellerin isimlerini ve telefon numaralarını al
    personeller = gonderim.personeller  # Personel listesi
    personel_data = [{'isim': f"{p.ad} {p.soyad}",
                      'telefon': p.telefon} for p in personeller]

    return render_template('paylasilan_gonderim.html', isyeri_adresi=isyeri_adresi, personel_data=personel_data)


@app.route('/log_share/<int:gonderim_id>')
@login_required
@log_action(lambda current_user, **kwargs: f"Kullanıcı ID {current_user.id} gönderim ID {kwargs.get('gonderim_id')} paylaştı.")
def log_and_share_gonderim(gonderim_id):
    # Doğrudan paylaşım sayfasına yönlendirin
    return redirect(url_for('edit_gonderim', gonderim_id=gonderim_id))


@app.route('/change_language/<language>')
def change_language(language):
    if language not in app.config['BABEL_SUPPORTED_LOCALES']:
        language = 'tr'  # Desteklenmeyen bir dil seçilmişse, varsayılan dili kullan
    session['lang'] = language
    return redirect(request.referrer or url_for('index'))


@app.route('/edit_house/<int:house_id>', methods=['GET', 'POST'])
@login_required
@log_action(lambda current_user, **kwargs: f"Ev Düzenleme: Ev ID {kwargs.get('house_id', 0)}")
def edit_house(house_id):
    if current_user.role not in ['admin', 'manager']:
        flash('Bu sayfaya erişim yetkiniz yok.', 'danger')
        return redirect(url_for('index'))

    # Ev bilgilerini veritabanından al
    house = House.query.get_or_404(house_id)
    form = EditHouseForm(obj=house)

    if house.house_type == 'company':
        house.rental_price = 0
    else:
        house.rental_price = form.rental_price.data

    if form.validate_on_submit():
        # Ev bilgilerini güncelle
        house.address = form.address.data
        house.capacity = form.capacity.data
        house.insurance_status = form.insurance_status.data
        house.insurance_date = form.insurance_date.data
        house.vve_meeting_date = form.vve_meeting_date.data
        house.house_type = form.house_type.data
        house.rental_price = form.rental_price.data

        # Fotoğrafları güncelle
        if form.photos.data:
            photo_paths = house.photos_list
            for photo in form.photos.data:
                if photo and allowed_file(photo.filename):
                    filename = secure_filename(photo.filename)
                    extension = os.path.splitext(filename)[1]
                    photo_filename = f"{uuid.uuid4()}{extension}"
                    photo_path = os.path.join(
                        app.config['UPLOAD_FOLDER'], photo_filename)
                    photo.save(photo_path)
                    photo_paths.append(os.path.join(
                        'static/uploads', photo_filename))
            house.photos = json.dumps(photo_paths)

        # Sigorta belgelerini güncelle
        if form.insurance_documents.data:
            document_paths = house.insurance_documents_list
            for document in form.insurance_documents.data:
                if document and allowed_file(document.filename, {'pdf'}):
                    filename = secure_filename(document.filename)
                    extension = os.path.splitext(filename)[1]
                    document_filename = f"{uuid.uuid4()}{extension}"
                    document_path = os.path.join(
                        app.config['UPLOAD_FOLDER'], document_filename)
                    document.save(document_path)
                    document_paths.append(os.path.join(
                        'static/uploads', document_filename))
            house.insurance_documents = json.dumps(document_paths)

        # Kira kontratını güncelle
        if form.rental_contract.data:
            rental_contract = form.rental_contract.data
            if rental_contract and allowed_file(rental_contract.filename, {'pdf'}):
                filename = secure_filename(rental_contract.filename)
                extension = os.path.splitext(filename)[1]
                rental_contract_filename = f"{uuid.uuid4()}{extension}"
                rental_contract_path = os.path.join(
                    app.config['UPLOAD_FOLDER'], rental_contract_filename)
                rental_contract.save(rental_contract_path)
                # Eski kira kontratını sil
                if house.rental_contract_path:
                    old_path = os.path.join(
                        app.root_path, house.rental_contract_path)
                    if os.path.exists(old_path):
                        os.remove(old_path)
                house.rental_contract_path = os.path.join(
                    'static/uploads', rental_contract_filename)

        try:
            db.session.commit()
            flash('Ev bilgileri başarıyla güncellendi.', 'success')
            return redirect(url_for('edit_house', house_id=house.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Ev bilgileri güncellenirken bir hata oluştu: {
                  e}', 'danger')

    # GET isteği için veya form doğrulama başarısız olduğunda
    current_year, current_week = get_current_year_week()

    # Her personel için kira kaydı olduğundan emin ol
    for house_personel in house.house_personeller:
        rent_payment = RentPayment.query.filter_by(
            house_id=house.id,
            personel_id=house_personel.personel.id,
            year=current_year,
            week_number=current_week
        ).first()

        if not rent_payment:
            new_rent_payment = RentPayment(
                house_id=house.id,
                personel_id=house_personel.personel.id,
                year=current_year,
                week_number=current_week,
                weekly_rent=house_personel.weekly_rent,
                paid=False
            )
            db.session.add(new_rent_payment)

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f'Kira kayıtları oluşturulurken bir hata oluştu: {e}', 'danger')

    # Güncel kira ödemelerini getir
    rent_payments = RentPayment.query.filter_by(
        house_id=house.id,
        year=current_year,
        week_number=current_week
    ).all()

    # Personellerin kira bilgilerini güncelle
    for hp in house.house_personeller:
        payment = next(
            (p for p in rent_payments if p.personel_id == hp.personel.id), None)
        if payment and payment.weekly_rent != hp.weekly_rent:
            payment.weekly_rent = hp.weekly_rent
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                flash(f'Kira miktarı güncellenirken bir hata oluştu: {
                      e}', 'danger')

    return render_template('edit_house.html',
                           house=house,
                           form=form,
                           rent_info=rent_payments,
                           current_year=current_year,
                           current_week=current_week)


@app.template_filter('country_name')
def country_name_filter(alpha_2_code):
    try:
        country = pycountry.countries.get(alpha_2=alpha_2_code.upper())
        return country.name if country else alpha_2_code
    except:
        return alpha_2_code


@app.route('/mark_rent_paid/<int:house_id>/<int:personel_id>', methods=['POST'])
@login_required
def mark_rent_paid(house_id, personel_id):
    if current_user.role not in ['admin', 'manager']:
        flash('Bu işlemi yapma yetkiniz yok.', 'danger')
        return redirect(url_for('index'))

    year, week_number = get_current_year_week()

    house_personel = HousePersonel.query.filter_by(
        house_id=house_id,
        personel_id=personel_id
    ).first()

    if not house_personel:
        flash('Personel bu evde kayıtlı değil.', 'danger')
        return redirect(url_for('edit_house', house_id=house_id))

    # Kira ödemesi kaydını bul veya oluştur
    rent_payment = RentPayment.query.filter_by(
        house_id=house_id,
        personel_id=personel_id,
        year=year,
        week_number=week_number
    ).first()

    if not rent_payment:
        rent_payment = RentPayment(
            house_id=house_id,
            personel_id=personel_id,
            year=year,
            week_number=week_number,
            weekly_rent=house_personel.weekly_rent,
            paid=True
        )
        db.session.add(rent_payment)
    else:
        rent_payment.paid = True

    # Log kaydı oluştur
    rent_log = RentLog(
        house_id=house_id,
        personel_id=personel_id,
        year=year,
        week_number=week_number,
        action='paid',
        amount=house_personel.weekly_rent,
        user_id=current_user.id
    )
    db.session.add(rent_log)

    try:
        db.session.commit()
        flash(f'{year} yılı {
              week_number}. hafta kirası ödendi olarak işaretlendi.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Bir hata oluştu: {e}', 'danger')

    return redirect(url_for('edit_house', house_id=house_id))


@app.route('/mark_rent_unpaid/<int:house_id>/<int:personel_id>', methods=['POST'])
@login_required
def mark_rent_unpaid(house_id, personel_id):
    if current_user.role not in ['admin', 'manager']:
        flash('Bu işlemi yapma yetkiniz yok.', 'danger')
        return redirect(url_for('index'))

    year, week_number = get_current_year_week()

    # HousePersonel kaydından haftalık kira miktarını al
    house_personel = HousePersonel.query.filter_by(
        house_id=house_id,
        personel_id=personel_id
    ).first()

    if not house_personel:
        flash('Personel bu evde kayıtlı değil.', 'warning')
        return redirect(url_for('edit_house', house_id=house_id))

    rent_payment = RentPayment.query.filter_by(
        house_id=house_id,
        personel_id=personel_id,
        year=year,
        week_number=week_number
    ).first()

    if rent_payment:
        rent_payment.paid = False

        # Log kaydı oluştur
        rent_log = RentLog(
            house_id=house_id,
            personel_id=personel_id,
            year=year,
            week_number=week_number,
            action='unpaid',
            amount=house_personel.weekly_rent,
            user_id=current_user.id
        )
        db.session.add(rent_log)

        try:
            db.session.commit()
            flash(f'{year} yılı {
                  week_number}. hafta kirası ödenmedi olarak işaretlendi.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Bir hata oluştu: {e}', 'danger')
    else:
        flash('Kira ödemesi kaydı bulunamadı.', 'warning')

    return redirect(url_for('edit_house', house_id=house_id))

# personel_takip.py'daki izin_ekle route'unu tamamlayalım


@app.route('/izin_ekle/<int:personel_id>', methods=['POST'])
@login_required
@log_action(lambda current_user, **kwargs: f"İzin Ekleme: Personel ID {kwargs.get('personel_id', 0)}")
def izin_ekle(personel_id):
    if current_user.role not in ['admin', 'manager']:
        flash('Bu işlemi yapma yetkiniz yok.', 'danger')
        return redirect(url_for('index'))

    form = IzinEkleForm()
    if form.validate_on_submit():
        try:
            # Personeli al
            personel = Personel.query.get_or_404(personel_id)

            # Aktif izin kontrolü
            if personel.aktif_izin:
                flash('Personelin aktif bir izni bulunmaktadır.', 'danger')
                return redirect(url_for('edit_personel', personel_id=personel_id))

            # Tarih kontrolü
            if form.baslangic_tarihi.data > form.bitis_tarihi.data:
                flash('Başlangıç tarihi bitiş tarihinden sonra olamaz.', 'danger')
                return redirect(url_for('edit_personel', personel_id=personel_id))

            # Yeni bir session başlat
            with db.session.begin_nested():
                # Eski iş yeri bilgisini sakla
                eski_sirket_id = personel.calistigi_sirket_id

                # Yeni izin kaydını oluştur
                yeni_izin = IzinKaydi(
                    personel_id=personel_id,
                    baslangic_tarihi=form.baslangic_tarihi.data,
                    bitis_tarihi=form.bitis_tarihi.data,
                    izin_turu=form.izin_turu.data,
                    durum='aktif',
                    aciklama=form.aciklama.data,
                    olusturan_id=current_user.id
                )
                db.session.add(yeni_izin)

                # Personeli pasife al ve iş yerinden çıkar
                personel.calistigi_sirket_id = None

                # Eğer personel bir gönderimde yer alıyorsa çıkar
                if eski_sirket_id:
                    gonderim = Gonderim.query.filter_by(
                        isyeri_id=eski_sirket_id).first()
                    if gonderim and personel in gonderim.personeller:
                        gonderim.personeller.remove(personel)

                        # İzne çıkma logunu ekle
                        gonderim_log = GonderimLog(
                            gonderim_id=gonderim.id,
                            personel_id=personel.id,
                            islem_tipi='izne_cikti',
                            tarih=datetime.utcnow()
                        )
                        db.session.add(gonderim_log)

            # Ana transaction'ı commit et
            db.session.commit()

            flash(f'Personel başarıyla izne çıkarıldı. İzin bitiş tarihi: {
                  form.bitis_tarihi.data.strftime("%d.%m.%Y")}', 'success')

        except Exception as e:
            db.session.rollback()
            print(f"DEBUG - İzin ekleme hatası: {str(e)}")
            flash(f'İzin kaydı oluşturulurken bir hata oluştu: {
                  str(e)}', 'danger')

        return redirect(url_for('edit_personel', personel_id=personel_id))

    # Form validation hataları için
    for field, errors in form.errors.items():
        for error in errors:
            flash(f'{getattr(form, field).label.text}: {error}', 'danger')

    return redirect(url_for('edit_personel', personel_id=personel_id))


@app.route('/izin_bitir/<int:izin_id>', methods=['POST'])
@login_required
@log_action(lambda current_user, **kwargs: f"İzin Bitirme: İzin ID {kwargs.get('izin_id', 0)}")
def izin_bitir(izin_id):
    if current_user.role not in ['admin', 'manager']:
        flash('Bu işlemi yapma yetkiniz yok.', 'danger')
        return redirect(url_for('index'))

    izin = IzinKaydi.query.get_or_404(izin_id)
    personel = izin.personel

    try:
        # İzni tamamlandı olarak işaretle
        izin.durum = 'tamamlandi'

        # Personeli aktif yap
        personel.status = 'Aktif'

        # İzin bitişini logla
        gonderim_log = GonderimLog(
            personel_id=personel.id,
            islem_tipi='izin_bitis',
            tarih=datetime.utcnow()
        )
        db.session.add(gonderim_log)

        db.session.commit()
        flash('İzin başarıyla sonlandırıldı. Personel tekrar aktif duruma geçirildi.', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'İzin sonlandırılırken bir hata oluştu: {e}', 'danger')

    return redirect(url_for('edit_personel', personel_id=personel.id))


@app.route('/update_leave_settings', methods=['POST'])
@login_required
@log_action(lambda current_user, **kwargs: "İzin Ayarları Güncelleme")
def update_leave_settings():
    if current_user.role != 'admin':
        flash('Bu işlemi yapma yetkiniz yok.', 'danger')
        return redirect(url_for('index'))

    form = IzinAyarlariForm()
    if form.validate_on_submit():
        try:
            # Mevcut ayarları bul veya yeni oluştur
            settings = IzinAyarlari.query.first()
            if not settings:
                settings = IzinAyarlari()
                db.session.add(settings)

            settings.saat_esigi = form.saat_esigi.data
            settings.ay_esigi = form.ay_esigi.data
            settings.izin_gun_sayisi = form.izin_gun_sayisi.data
            settings.updated_at = datetime.utcnow()

            db.session.commit()
            flash('İzin ayarları başarıyla güncellendi.', 'success')

        except Exception as e:
            db.session.rollback()
            flash(f'Ayarlar güncellenirken bir hata oluştu: {e}', 'danger')

    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f'{getattr(form, field).label.text}: {error}', 'danger')

    return redirect(url_for('yonetici_ayarlari', tab='izin-ayarlari'))


@app.route('/debug_personel/<int:personel_id>')
@login_required
def debug_personel(personel_id):
    personel = Personel.query.get(personel_id)
    if personel:
        return {
            'id': personel.id,
            'ad': personel.ad,
            'soyad': personel.soyad,
            'calistigi_sirket_id': personel.calistigi_sirket_id,
            'sirket_adi': personel.calistigi_sirket.ad if personel.calistigi_sirket else None,
            'aktif_izin': bool(personel.aktif_izin)
        }
    return {'error': 'Personel bulunamadı'}


# Uygulamayı Çalıştırma
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=False)
