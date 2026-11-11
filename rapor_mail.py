#!/usr/bin/env python3
"""Atlanan ürün raporunu (atlanan-urunler.csv) Gmail SMTP ile mail atar.

Gerekli ortam değişkenleri (GitHub secret): MAIL_KULLANICI, MAIL_SIFRE (Gmail uygulama şifresi), MAIL_ALICI.
Tanımlı değillerse sessizce atlar.
"""
import collections
import csv
import html
import os
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
import xml.etree.ElementTree as ET

ORNEK = 30  # her nedenden gövdede gösterilecek ürün sayısı; tamamı ekte


def main():
    yol = Path(sys.argv[1] if len(sys.argv) > 1 else "atlanan-urunler.csv")
    kullanici, sifre, alici = (os.environ.get(k, "").strip() for k in ("MAIL_KULLANICI", "MAIL_SIFRE", "MAIL_ALICI"))
    if not (kullanici and sifre and alici):
        print("Mail bilgileri tanımlı değil; rapor maili atlandı.")
        return
    satirlar = list(csv.DictReader(open(yol, encoding="utf-8-sig"), delimiter=";"))
    gruplar = collections.defaultdict(list)
    for s in satirlar:
        gruplar[s["neden"]].append(s)
    aktarilan = "?"
    if len(sys.argv) > 2 and Path(sys.argv[2]).exists():  # yayınlanan XML: aktarılan ürün sayısı
        aktarilan = sum(1 for _, e in ET.iterparse(sys.argv[2]) if e.tag == "Urun")
    zaman = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m.%Y %H:%M")

    parca = [f"<p><b>{zaman}</b> çalışması: siteye aktarılan <b>{aktarilan}</b> ürün, "
             f"aktarılmayan <b>{len(satirlar)}</b> ürün.</p><ul>"]
    parca += [f"<li>{html.escape(n)}: <b>{len(g)}</b></li>" for n, g in sorted(gruplar.items(), key=lambda x: -len(x[1]))]
    parca.append("</ul><p>Tam liste ekteki CSV dosyasında (Excel ile açılır).</p>")
    for neden, g in sorted(gruplar.items(), key=lambda x: -len(x[1])):
        parca.append(f"<h3>{html.escape(neden)} ({len(g)})</h3><table border='1' cellpadding='4' "
                     "style='border-collapse:collapse;font-size:12px'><tr><th>Ürün</th><th>Model kodu</th>"
                     "<th>Stok kodları</th><th>Barkod</th><th>Detay</th><th>Toptancı kategorisi</th></tr>")
        for s in g[:ORNEK]:
            stok = s["stok_kodlari"].split()
            stok_metin = " ".join(stok[:3]) + (f" … (+{len(stok) - 3})" if len(stok) > 3 else "")
            barkod = s["barkodlar"].split()
            barkod_metin = " ".join(barkod[:3]) + (f" … (+{len(barkod) - 3})" if len(barkod) > 3 else "")
            parca.append("<tr>" + "".join(f"<td>{html.escape(v)}</td>" for v in (
                s["urun_adi"], s["model_kodu"], stok_metin, barkod_metin, s["detay"], s["toptanci_kategori"])) + "</tr>")
        parca.append("</table>")
        if len(g) > ORNEK:
            parca.append(f"<p>… ve {len(g) - ORNEK} ürün daha (ekte).</p>")

    m = EmailMessage()
    m["Subject"] = f"Saveru XML: {len(satirlar)} ürün aktarılmadı ({zaman})"
    m["From"] = kullanici
    m["To"] = alici
    m.set_content("Rapor HTML olarak hazırlandı; tam liste ekteki CSV dosyasında.")
    m.add_alternative("".join(parca), subtype="html")
    m.add_attachment(yol.read_bytes(), maintype="text", subtype="csv",
                     filename=f"atlanan-urunler-{datetime.now():%Y%m%d-%H%M}.csv")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(kullanici, sifre)
        smtp.send_message(m)
    print(f"Rapor maili gönderildi ({len(satirlar)} ürün).")


if __name__ == "__main__":
    main()
