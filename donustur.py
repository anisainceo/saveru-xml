#!/usr/bin/env python3
"""Çarşı Altın (Faprika) XML'ini Saveru için Ticimax formatına dönüştürür.

Kullanım:
    KAYNAK_URL=... python3 donustur.py  # toptancı linkinden çeker
    python3 donustur.py kaynak.xml      # yerel dosyadan okur
Çıktı: saveru-urunler.xml (+ eslesmeyen-kategoriler.txt)
"""
import csv
import html
import http.client
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

KAYNAK_URL = os.environ.get("KAYNAK_URL")  # toptancı linki, GitHub'da gizli (secret) olarak saklanır
HAS_CARPANI = 0.995      # indirimli fiyat (müşterinin ödediği) = gram × 0,995 has; TL'ye Ticimax HAS kuruyla çevirir
SATIS_CARPANI = 1.25     # üstü çizili satış fiyatı = indirimli × 1,25 → etikette %20 indirim görünür
PARA_BIRIMI = "HAS"
MARKA = "Saveru"
STOK_KODU_ONEK = "CA-"   # dükkan ürünlerinden ayırt etmek için
HARIC_AYARLAR = {"22"}   # şimdilik satılmayan ayarlar; 22 ayar satışa açılınca boş küme yap
# Hiç aktarılmayacak markalar (bayisi değiliz, telif riski). 27.11.2026: Cetaş ve Ema Jewellery.
# Üç ayrı kontrol; biri tutması yeterli:
#   1) Toptancı kategori yolunda marka adı geçiyor (ör. "Koleksiyonlar >> Ema Jewellery")
#   2) Ürün adında / açıklamasında marka adı geçiyor
#   3) Model kodu Cetaş kod biçiminde: X + (en çok 2 harf) + rakam, ör. X11180NAV1, XD10563NAAY, XF11680BAAY
#      (27.11.2026'da bu biçimdeki 317 ürünün 316'sı Cetaş kataloğu/bayileriyle doğrulandı)
HARIC_MARKA_KELIMELERI = ("cetaş", "cetas", "ema jewel", "çağla x ema", "cagla x ema")
HARIC_KOD_DESENI = re.compile(r"^X[A-Z]{0,2}\d", re.I)
#   4) Barkodda Cetaş öneki var: 869CTS… (X ile başlamayan Cetaş kodlarını da yakalar, ör. 37-140NAAY, Z2431NAAY)
HARIC_BARKOD_PARCALARI = ("CTS",)
# Toptancı kategorisi -> Ticimax etiketi (sayfa adresi). Bu sayfalar Ticimax'te etiketle yönetiliyor;
# ek_kategori.py etiketleri web servisiyle ürünlere yazar.
ETIKETLER = {
    "Trend Ürünler": "/trend-urunler",
    "Aynı Gün Kargo": "/ayni-gun-kargo",
    "İndirimdekiler": "/indirimdekiler",
    "Yeniler": "/yeni-gelenler",
    "Çok Satanlar": "/cok-satanlar",
}

KLASOR = Path(__file__).resolve().parent
CIKTI_KLASOR = Path(os.environ.get("CIKTI_KLASOR", KLASOR))
CIKTI = CIKTI_KLASOR / "saveru-urunler.xml"
ESLESTIRME = KLASOR / "kategori-eslestirme.csv"
ESLESMEYEN = CIKTI_KLASOR / "eslesmeyen-kategoriler.txt"
# Atlanan ürün raporu yayın klasörüne (herkese açık) KONMAZ; mail ile gönderilir
ATLANAN = Path(os.environ.get("RAPOR_KLASOR", KLASOR)) / "atlanan-urunler.csv"

OLCU_ADLARI = {
    "Lütfen yüzük ölçüsü seçiniz.": "Yüzük Ölçüsü",
    "Lütfen bilezik ölçüsü seçiniz.": "Bilezik Ölçüsü",
    "Lütfen zincir ölçüsü seçiniz.": "Zincir Ölçüsü",
}


def sayi(metin):
    try:
        return float((metin or "").replace(",", ".").strip())
    except ValueError:
        return 0.0


def tr_sayi(deger, basamak=2):
    return f"{deger:.{basamak}f}".replace(".", ",")


def bosluk(metin):
    return re.sub(r"\s+", " ", metin or "").strip()


def eslestirme_oku():
    """Satır sırası önceliktir: ürünün eşleşen ilk satırı ana kategori olur."""
    sira = []
    with open(ESLESTIRME, encoding="utf-8-sig") as f:
        for satir in csv.DictReader(f, delimiter=";"):
            sira.append((bosluk(satir["toptanci_kategori"]), satir["saveru_kategori"].strip()))
    return sira


def aciklama_metni(ham):
    """Toptancının 'None' dolu tablosundan sadece paragraf metnini alır."""
    paragraflar = []
    for p in re.findall(r"<p[^>]*>(.*?)</p>", html.unescape(ham or ""), re.S):
        metin = bosluk(re.sub(r"<[^>]+>", " ", p).replace("\xa0", " "))
        if metin:
            paragraflar.append(metin)
    return paragraflar


def seo_baslik(ad, ayar, gram):
    baslik = f"{ad} {ayar} Ayar {tr_sayi(gram)} gr | Saveru"
    if len(baslik) > 65:
        baslik = f"{ad} | Saveru"
    return baslik


def seo_aciklama(ad, ayar, gram):
    return (f"{ayar} ayar {tr_sayi(gram)} gram {ad}. Günlük altın kuruyla anlık fiyat, "
            f"1988'den beri İnceoğlu Kuyumculuk güvencesi, ücretsiz kargo ve 14 gün iade Saveru'da.")


def alt(ust, ad, metin=None, **attrib):
    e = ET.SubElement(ust, ad, attrib)
    if metin is not None:
        e.text = str(metin)
    return e


def kucuk(metin):
    return (metin or "").replace("İ", "i").replace("I", "ı").lower()


def haric_marka(p):
    """Ürün hariç tutulan bir markaya aitse nedenini, değilse boş döndürür."""
    yollar = [kucuk(c.get("Path")) for c in (p.find("Categories") or [])]
    for w in HARIC_MARKA_KELIMELERI:
        if any(w in y for y in yollar):
            return f"kategori: {w}"
    metin = kucuk(p.get("Name")) + " " + kucuk(p.get("FullDescription"))
    for w in HARIC_MARKA_KELIMELERI:
        if w in metin:
            return f"ad/açıklama: {w}"
    kodlar = [p.get("Sku") or ""] + [k.get("Sku") or "" for k in (p.find("Combinations") or [])]
    for k in kodlar:
        if HARIC_KOD_DESENI.match(k.strip()):
            return f"Cetaş kod biçimi: {k}"
    barkodlar = [p.get("Gtin") or ""] + [k.get("Gtin") or "" for k in (p.find("Combinations") or [])]
    for b in barkodlar:
        if any(parca in b.upper() for parca in HARIC_BARKOD_PARCALARI):
            return f"Cetaş barkodu: {b}"
    return ""


def donustur(kaynak_xml):
    eslestirme = eslestirme_oku()
    bilinen = {k for k, _ in eslestirme}
    eslesmeyen = {}
    root = ET.Element("Root")
    urunler = alt(root, "Urunler")
    sayac = {"aktarilan": 0, "yayinda_degil": 0, "gramsiz": 0, "kategorisiz": 0, "haric_ayar": 0, "haric_urun": 0}
    atlanan = []

    def atla(p, anahtar, neden, detay=""):
        sayac[anahtar] += 1
        kombinasyonlar = list(p.find("Combinations")) or [p]
        atlanan.append({
            "neden": neden,
            "detay": detay,
            "urun_adi": bosluk(p.get("Name")),
            "model_kodu": p.get("Sku") or "",
            "stok_kodlari": " ".join(STOK_KODU_ONEK + (k.get("Sku") or p.get("Sku") or "") for k in kombinasyonlar),
            "barkodlar": " ".join(dict.fromkeys(k.get("Gtin") or "" for k in kombinasyonlar if k.get("Gtin"))),
            "toptanci_id": p.get("Id"),
            "toptanci_kategori": " | ".join(bosluk(c.get("Path")) for c in p.find("Categories")),
            "toptanci_link": p.get("Url") or "",
        })

    for p in ET.fromstring(kaynak_xml).findall("Product"):
        if p.get("Published") != "True":
            atla(p, "yayinda_degil", "Toptancıda yayında değil")
            continue
        neden = haric_marka(p)
        if neden:
            atla(p, "haric_urun", "Hariç tutulan marka (Cetaş/Ema)", neden)
            continue
        ozellik = {s.get("Name"): s.get("Value") for s in p.find("Specifications")}
        gram = sayi(ozellik.get("Gram"))
        if gram <= 0:
            ham = ozellik.get("Gram")
            atla(p, "gramsiz", "Gram bilgisi yok/okunamadı",
                 f"Gram alanı: {ham!r}" if ham else "Özellik alanında gram yok")
            continue
        ayar = re.sub(r"\D", "", ozellik.get("Ayar", "")) or "14"
        if ayar in HARIC_AYARLAR:
            atla(p, "haric_ayar", f"{ayar} ayar (şimdilik satışa kapalı)", f"{tr_sayi(gram)} gr")
            continue
        ad = bosluk(p.get("Name"))

        yollar = [bosluk(c.get("Path")) for c in p.find("Categories")]
        for y in yollar:
            if y not in bilinen:
                eslesmeyen[y] = eslesmeyen.get(y, 0) + 1
        kategoriler = []
        for toptanci, saveru in eslestirme:
            if saveru and toptanci in yollar and saveru not in kategoriler:
                kategoriler.append(saveru)
        if not kategoriler:
            atla(p, "kategorisiz", "Kategori eşleşmedi", "Eşleştirme tablosunda olmayan toptancı kategorisi")
            continue
        # Hediye alt kategorisindeki ürün /hediye ana sayfasında da listelensin
        if any(k.startswith("Hediye/") for k in kategoriler) and "Hediye" not in kategoriler:
            kategoriler.append("Hediye")
        ana = kategoriler[0].split("/")[0]
        if ayar == "22" and ana in ("Kolye", "Zincir", "Yüzük", "Küpe"):
            yirmiiki = f"{ana}/22 Ayar Altın {ana}"
            if yirmiiki not in kategoriler:
                kategoriler.append(yirmiiki)

        indirimli = round(gram * HAS_CARPANI, 3)
        satis = round(indirimli * SATIS_CARPANI, 3)

        paragraflar = aciklama_metni(p.get("FullDescription"))
        renk = ozellik.get("Renk", "")
        detay = [f"<li><strong>Ayar:</strong> {ayar} Ayar</li>",
                 f"<li><strong>Gram:</strong> {tr_sayi(gram)} gr</li>"]
        if renk:
            detay.append(f"<li><strong>Renk:</strong> {html.escape(renk)}</li>")
        aciklama = (f"<p>{html.escape(ad)}, {ayar} ayar altından {tr_sayi(gram)} gram olarak üretilmiştir.</p>"
                    f"<ul>{''.join(detay)}</ul>"
                    + "".join(f"<p>{html.escape(x)}</p>" for x in paragraflar))

        u = alt(urunler, "Urun")
        alt(u, "UrunKartiID", p.get("Id"))
        alt(u, "UrunAdi", ad)
        alt(u, "OnYazi", "")
        alt(u, "Aciklama", aciklama)
        alt(u, "Marka", MARKA)
        alt(u, "SatisBirimi", "ADET")
        alt(u, "Kategori", kategoriler[0].split("/")[-1])
        alt(u, "KategoriTree", kategoriler[0])
        ek = alt(u, "EkKategoriler")
        for k in kategoriler[1:]:
            alt(ek, "KategoriTree", k)
        # Connect "Kategorileri Çoklu Alandan Oku": kök Kategoriler, öğe KategoriTree (ana + ek hepsi)
        hepsi = alt(u, "Kategoriler")
        for k in kategoriler:
            alt(hepsi, "KategoriTree", k)
        etiketler = alt(u, "Etiketler")
        for toptanci, url in ETIKETLER.items():
            if toptanci in yollar:
                alt(etiketler, "Etiket", toptanci, Url=url)
        alt(u, "Gram", f"{gram:.2f}")
        alt(u, "OzelAlan1", tr_sayi(gram))
        alt(u, "SeoBaslik", seo_baslik(ad, ayar, gram))
        alt(u, "SeoAciklama", seo_aciklama(ad, ayar, gram))
        alt(u, "SeoAnahtarKelime", f"{ayar} ayar altın, {ad.lower()}, {kategoriler[0].split('/')[-1].lower()}")
        resimler = alt(u, "Resimler")
        for r in p.find("Pictures"):
            alt(resimler, "Resim", r.get("Path"))

        secenekler = alt(u, "UrunSecenek")
        kombinasyonlar = list(p.find("Combinations")) or [p]
        for k in kombinasyonlar:
            s = alt(secenekler, "Secenek")
            alt(s, "VaryasyonID", k.get("Id"))
            alt(s, "StokKodu", STOK_KODU_ONEK + (k.get("Sku") or p.get("Sku")))
            alt(s, "Barkod", k.get("Gtin") or "")
            alt(s, "StokAdedi", k.get("StockQuantity") or "0")
            alt(s, "AlisFiyati", "0.000")
            alt(s, "SatisFiyati", f"{satis:.3f}")
            alt(s, "IndirimliFiyat", f"{indirimli:.3f}")
            alt(s, "KDVDahil", "true")
            alt(s, "KdvOrani", "0")
            alt(s, "ParaBirimiKodu", PARA_BIRIMI)
            alt(s, "Desi", "1")
            ekozellik = alt(s, "EkSecenekOzellik")
            for a in k.iter("Attribute"):
                tanim = OLCU_ADLARI.get(a.get("Name"), a.get("Name"))
                alt(ekozellik, "Ozellik", a.get("Value"), Tanim=tanim, Deger=a.get("Value"))

        teknik = alt(u, "TeknikDetaylar")
        for tanim, deger in (("Ayar", f"{ayar} Ayar"), ("Renk", renk),
                             ("Cinsiyet", ozellik.get("Cinsiyet", "")), ("Gram", f"{tr_sayi(gram)} gr")):
            if deger:
                td = alt(teknik, "TeknikDetay")
                alt(td, "OzellikTanim", tanim)
                alt(td, "DegerTanim", deger)
        sayac["aktarilan"] += 1

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(CIKTI, encoding="utf-8", xml_declaration=True)
    ESLESMEYEN.write_text(
        "".join(f"{y};{n} ürün\n" for y, n in sorted(eslesmeyen.items(), key=lambda x: -x[1])),
        encoding="utf-8")
    ATLANAN.parent.mkdir(parents=True, exist_ok=True)
    with open(ATLANAN, "w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig: Excel Türkçe karakterleri doğru açsın
        yaz = csv.DictWriter(f, fieldnames=list(atlanan[0]) if atlanan else ["neden"], delimiter=";")
        yaz.writeheader()
        yaz.writerows(atlanan)
    return sayac, eslesmeyen


def indir(url, deneme=4):
    """Toptancı sunucusu bağlantıyı yarıda kesebiliyor; birkaç kez yeniden dener."""
    for i in range(1, deneme + 1):
        try:
            istek = urllib.request.Request(url, headers={"User-Agent": "Saveru-XML/1.0"})
            with urllib.request.urlopen(istek, timeout=180) as yanit:
                return yanit.read()
        except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError, ConnectionError) as hata:
            if i == deneme:
                raise
            print(f"İndirme hatası ({type(hata).__name__}), {i * 30} sn sonra tekrar denenecek ({i}/{deneme})")
            time.sleep(i * 30)


def main():
    if len(sys.argv) > 1:
        kaynak = Path(sys.argv[1]).read_bytes()
    else:
        if not KAYNAK_URL:
            sys.exit("KAYNAK_URL ortam değişkeni tanımlı değil.")
        kaynak = indir(KAYNAK_URL)
    CIKTI_KLASOR.mkdir(parents=True, exist_ok=True)
    sayac, eslesmeyen = donustur(kaynak)
    if sayac["aktarilan"] < 100:
        # Toptancı boş/bozuk XML dönerse yayındaki dosyayı ezmemek için hata ver
        sys.exit(f"Çok az ürün aktarıldı ({sayac['aktarilan']}), yayın durduruldu.")
    print(f"Aktarılan ürün: {sayac['aktarilan']}")
    print(f"Toptancıda yayında olmadığı için atlanan: {sayac['yayinda_degil']}")
    print(f"Gramı olmadığı için atlanan: {sayac['gramsiz']}")
    print(f"Satışa kapalı ayar ({', '.join(sorted(HARIC_AYARLAR))}) olduğu için atlanan: {sayac['haric_ayar']}")
    print(f"Kategorisi eşleşmediği için atlanan: {sayac['kategorisiz']}")
    print(f"Hariç tutulan marka (Cetaş/Ema): {sayac['haric_urun']}")
    if eslesmeyen:
        print(f"Tabloda olmayan {len(eslesmeyen)} toptancı kategorisi var -> {ESLESMEYEN.name}")
    print(f"Çıktı: {CIKTI}")


if __name__ == "__main__":
    main()
