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
INDIRIMLI_FARK = 0.300   # indirimli fiyat (müşterinin ödediği) = gram + 0,300 has
INDIRIM_ORANI = 0.20     # üstü çizili satış fiyatı = indirimli ÷ 0,80 → her üründe %20 indirim görünür
PARA_BIRIMI = "HAS"
MARKA = "Saveru"
STOK_KODU_ONEK = "CA-"   # dükkan ürünlerinden ayırt etmek için
HARIC_AYARLAR = {"22"}   # şimdilik satılmayan ayarlar; 22 ayar satışa açılınca boş küme yap

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


def donustur(kaynak_xml):
    eslestirme = eslestirme_oku()
    bilinen = {k for k, _ in eslestirme}
    eslesmeyen = {}
    root = ET.Element("Root")
    urunler = alt(root, "Urunler")
    sayac = {"aktarilan": 0, "yayinda_degil": 0, "gramsiz": 0, "kategorisiz": 0, "haric_ayar": 0}
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
        ana = kategoriler[0].split("/")[0]
        if ayar == "22" and ana in ("Kolye", "Zincir", "Yüzük", "Küpe"):
            yirmiiki = f"{ana}/22 Ayar Altın {ana}"
            if yirmiiki not in kategoriler:
                kategoriler.append(yirmiiki)

        indirimli = round(gram + INDIRIMLI_FARK, 3)
        satis = round(indirimli / (1 - INDIRIM_ORANI), 3)

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
    if eslesmeyen:
        print(f"Tabloda olmayan {len(eslesmeyen)} toptancı kategorisi var -> {ESLESMEYEN.name}")
    print(f"Çıktı: {CIKTI}")


if __name__ == "__main__":
    main()
