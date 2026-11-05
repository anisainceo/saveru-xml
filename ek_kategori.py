#!/usr/bin/env python3
"""XML'deki ek kategorileri Ticimax web servisiyle ürünlere ekler.

Ticimax Connect bir ürüne yalnızca tek (ana) kategori yazabiliyor. Bu betik,
saveru-urunler.xml'deki <Kategoriler> listesindeki diğer kategorileri
UrunServis.SaveUrunKategori ile ekler. Yalnızca EKLER, hiçbir kategoriyi silmez;
yalnızca XML'deki (CA- stok kodlu) ürünlere dokunur.

Kullanım:
    TICIMAX_WS_KODU=... python3 ek_kategori.py yayin/saveru-urunler.xml            # deneme: ne ekleneceğini yazar
    TICIMAX_WS_KODU=... python3 ek_kategori.py yayin/saveru-urunler.xml --uygula   # gerçekten ekler
    ... --limit 5   # yalnızca ilk 5 ürün (test için)
"""
import argparse
import os
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

SERVIS = os.environ.get("TICIMAX_SERVIS", "https://www.saveru.com.tr/Servis/UrunServis.svc")
NS_DC = "http://schemas.datacontract.org/2004/07/"
NS_ARR = "http://schemas.microsoft.com/2003/10/Serialization/Arrays"
SAYFA = 100


def yerel(etiket):
    return etiket.rsplit("}", 1)[-1]


def cocuk(e, ad):
    for c in e:
        if yerel(c.tag) == ad:
            return c
    return None


def metin(e, ad, varsayilan=""):
    c = cocuk(e, ad)
    return c.text if c is not None and c.text is not None else varsayilan


def soap(islem, govde, deneme=3):
    zarf = (
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        f'<s:Body><{islem} xmlns="http://tempuri.org/">{govde}</{islem}></s:Body></s:Envelope>'
    ).encode("utf-8")
    istek = urllib.request.Request(SERVIS, data=zarf, headers={
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": f'"http://tempuri.org/IUrunServis/{islem}"',
    })
    for i in range(deneme):
        try:
            with urllib.request.urlopen(istek, timeout=120) as yanit:
                return ET.fromstring(yanit.read())
        except urllib.error.HTTPError as hata:
            icerik = hata.read().decode("utf-8", "replace")
            if hata.code == 500 and "Fault" in icerik:
                sys.exit(f"{islem} hata verdi: {fault_mesaji(icerik)}")
            if i == deneme - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if i == deneme - 1:
                raise
        time.sleep(5 * (i + 1))


def fault_mesaji(icerik):
    try:
        for e in ET.fromstring(icerik).iter():
            if yerel(e.tag) in ("faultstring", "Text") and e.text:
                return e.text
    except ET.ParseError:
        pass
    return icerik[:300]


def uye(kod):
    return f"<UyeKodu>{escape(kod)}</UyeKodu>"


def kategori_yollari(kod):
    """Panel kategori ağacı: 'Yüzük/Sade & Minimal Yüzük' -> ID."""
    kok = soap("SelectKategori", uye(kod) + "<kategoriID>0</kategoriID>")
    kayitlar = {}
    for k in kok.iter():
        if yerel(k.tag) == "Kategori" and cocuk(k, "ID") is not None:
            kayitlar[int(metin(k, "ID"))] = (int(metin(k, "PID", "0") or 0), (metin(k, "Tanim") or "").strip())
    yollar = {}
    for kid in kayitlar:
        parcalar, simdiki, gorulen = [], kid, set()
        while simdiki in kayitlar and simdiki not in gorulen:
            gorulen.add(simdiki)
            pid, tanim = kayitlar[simdiki]
            parcalar.append(tanim)
            simdiki = pid
        yol = "/".join(reversed(parcalar))
        if yol in yollar:
            print(f"Uyarı: aynı yolda iki kategori var: {yol} ({yollar[yol]}, {kid}) — küçük ID kullanılacak")
            kid = min(kid, yollar[yol])
        yollar[yol] = kid
    return yollar


def panel_urunleri(kod):
    """CA- stok kodu -> (UrunKartiID, mevcut kategori ID kümesi)."""
    harita = {}
    baslangic = 0
    while True:
        filtre = (
            f'<f xmlns:a="{NS_DC}"><a:Aktif>-1</a:Aktif><a:Firsat>-1</a:Firsat><a:Indirimli>-1</a:Indirimli>'
            '<a:KategoriID>0</a:KategoriID><a:MarkaID>0</a:MarkaID><a:UrunKartiID>0</a:UrunKartiID>'
            '<a:Vitrin>-1</a:Vitrin></f>'
            f'<s xmlns:a="{NS_DC}"><a:BaslangicIndex>{baslangic}</a:BaslangicIndex>'
            f'<a:KayitSayisi>{SAYFA}</a:KayitSayisi><a:SiralamaDegeri>ID</a:SiralamaDegeri>'
            '<a:SiralamaYonu>ASC</a:SiralamaYonu></s>'
        )
        kok = soap("SelectUrun", uye(kod) + filtre)
        kartlar = [e for e in kok.iter() if yerel(e.tag) == "UrunKarti"]
        for kart in kartlar:
            kid = int(metin(kart, "ID", "0"))
            mevcut = set()
            kat = cocuk(kart, "Kategoriler")
            if kat is not None:
                mevcut = {int(x.text) for x in kat if x.text and x.text.strip().isdigit()}
            ana = metin(kart, "AnaKategoriID")
            if ana.isdigit():
                mevcut.add(int(ana))
            varyasyonlar = cocuk(kart, "Varyasyonlar")
            for v in (varyasyonlar if varyasyonlar is not None else []):
                sk = metin(v, "StokKodu").strip()
                if sk:
                    harita[sk] = (kid, mevcut)
        if len(kartlar) < SAYFA:
            return harita
        baslangic += SAYFA


def xml_urunleri(yol):
    """XML'den: (ürün adı, stok kodları, kategori yolları)."""
    sonuc = []
    for u in ET.parse(yol).getroot().iter("Urun"):
        kategoriler = [k.text for k in u.findall("Kategoriler/KategoriTree") if k.text]
        if not kategoriler:
            kategoriler = [u.findtext("KategoriTree")]
        stoklar = [s.text for s in u.findall("UrunSecenek/Secenek/StokKodu") if s.text]
        sonuc.append((u.findtext("UrunAdi"), stoklar, kategoriler))
    return sonuc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xml")
    ap.add_argument("--uygula", action="store_true", help="eklemeleri gerçekten yap")
    ap.add_argument("--limit", type=int, default=0, help="yalnızca ilk N ürün (test)")
    a = ap.parse_args()

    kod = os.environ.get("TICIMAX_WS_KODU", "").strip()
    if not kod:
        print("TICIMAX_WS_KODU tanımlı değil; ek kategori adımı atlandı.")
        return

    yollar = kategori_yollari(kod)
    print(f"Paneldeki kategori: {len(yollar)}")
    panel = panel_urunleri(kod)
    print(f"Paneldeki varyasyon stok kodu: {len(panel)}")

    eklenecek, bulunamayan_urun, bulunamayan_kat = [], 0, {}
    urunler = xml_urunleri(a.xml)
    if a.limit:
        urunler = urunler[:a.limit]
    for ad, stoklar, kategoriler in urunler:
        kart = next((panel[s] for s in stoklar if s in panel), None)
        if not kart:
            bulunamayan_urun += 1
            continue
        kid, mevcut = kart
        for yol in kategoriler:
            kat_id = yollar.get(yol)
            if kat_id is None:
                bulunamayan_kat[yol] = bulunamayan_kat.get(yol, 0) + 1
            elif kat_id not in mevcut:
                eklenecek.append((kid, kat_id, ad, yol))
                mevcut.add(kat_id)

    print(f"İncelenen ürün: {len(urunler)}, panelde bulunamayan: {bulunamayan_urun}")
    for yol, n in sorted(bulunamayan_kat.items(), key=lambda x: -x[1]):
        print(f"Panelde olmayan kategori: {yol} ({n} ürün)")
    print(f"Eklenecek ürün-kategori bağı: {len(eklenecek)}")
    for kid, kat_id, ad, yol in eklenecek[:15]:
        print(f"  {kid} {ad} -> {yol} ({kat_id})")

    if not a.uygula:
        print("Deneme modu: hiçbir şey yazılmadı (--uygula ile yazılır).")
        return
    for i in range(0, len(eklenecek), 100):
        parca = eklenecek[i:i + 100]
        liste = "".join(
            f"<a:UrunKategori><a:KategoriID>{kat_id}</a:KategoriID><a:Sira>0</a:Sira>"
            f"<a:UrunKartiID>{kid}</a:UrunKartiID></a:UrunKategori>"
            for kid, kat_id, _, _ in parca)
        soap("SaveUrunKategori", uye(kod) + f'<list xmlns:a="{NS_DC}">{liste}</list>')
        print(f"Yazıldı: {i + len(parca)}/{len(eklenecek)}")


if __name__ == "__main__":
    main()
