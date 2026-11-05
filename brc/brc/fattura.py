"""Italian FatturaPA (private) via python-a38. Demo documents, not fiscal."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal

import a38
from a38.validation import Validation

from brc.model import VAT_RATE, Party, eur, kg


def _party_supplier(party: Party) -> a38.CedentePrestatore:
    return a38.CedentePrestatore(
        a38.DatiAnagraficiCedentePrestatore(
            a38.IdFiscaleIVA(party.vat_country, party.vat),
            anagrafica=a38.Anagrafica(denominazione=party.name),
            regime_fiscale="RF01",
        ),
        a38.Sede(
            indirizzo=party.address,
            cap=party.cap,
            comune=party.city,
            provincia=party.province,
            nazione="IT",
        ),
        iscrizione_rea=a38.IscrizioneREA(ufficio="MI", numero_rea="0000000", stato_liquidazione="LN"),
    )


def _party_buyer(party: Party) -> a38.CessionarioCommittente:
    return a38.CessionarioCommittente(
        a38.DatiAnagraficiCessionarioCommittente(
            a38.IdFiscaleIVA(party.vat_country, party.vat),
            anagrafica=a38.Anagrafica(denominazione=party.name),
        ),
        a38.Sede(
            indirizzo=party.address,
            cap=party.cap,
            comune=party.city,
            provincia=party.province,
            nazione="IT",
        ),
    )


def _blank(supplier: Party, buyer: Party, progressivo: str) -> a38.FatturaPrivati12:
    fattura = a38.FatturaPrivati12()
    header = fattura.fattura_elettronica_header
    header.dati_trasmissione.id_trasmittente = a38.IdTrasmittente(supplier.vat_country, supplier.vat)
    header.dati_trasmissione.progressivo_invio = progressivo
    header.dati_trasmissione.codice_destinatario = "XNDEMO1"
    header.cedente_prestatore = _party_supplier(supplier)
    header.cessionario_committente = _party_buyer(buyer)
    return fattura


def _render(fattura: a38.FatturaPrivati12) -> str:
    validation = Validation()
    fattura.validate(validation)
    if validation.errors:
        joined = "; ".join(str(err) for err in validation.errors)
        raise ValueError(joined)
    root = fattura.build_etree()
    element = root.getroot() if hasattr(root, "getroot") else root
    xml = ET.tostring(element, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml


def build_invoice(
    supplier: Party,
    buyer: Party,
    number: str,
    order_id: str,
    ddt_number: str,
    description: str,
    qty_kg: Decimal,
    unit_price: Decimal,
    on: date,
    tipo: str = "TD01",
    linked_invoice: str | None = None,
) -> str:
    qty_kg = kg(qty_kg)
    unit_price = eur(unit_price)
    imponibile = eur(qty_kg * unit_price)
    fattura = _blank(supplier, buyer, number[:10])
    body = fattura.fattura_elettronica_body[0]
    body.dati_generali.dati_generali_documento = a38.DatiGeneraliDocumento(
        tipo_documento=tipo,
        divisa="EUR",
        data=on,
        numero=number,
        importo_totale_documento=None,
        causale=["Documento dimostrativo, non fiscale."],
    )
    body.dati_generali.dati_ordine_acquisto.append(
        a38.DatiOrdineAcquisto(id_documento=order_id, data=on, riferimento_numero_linea=[1])
    )
    body.dati_generali.dati_ddt.append(
        a38.DatiDDT(numero_ddt=ddt_number, data_ddt=on, riferimento_numero_linea=[1])
    )
    if linked_invoice:
        body.dati_generali.dati_fatture_collegate.append(
            a38.DatiFattureCollegate(id_documento=linked_invoice, data=on)
        )
    body.dati_beni_servizi.add_dettaglio_linee(
        descrizione=description[:1000],
        quantita=qty_kg,
        unita_misura="kg",
        prezzo_unitario=unit_price,
        prezzo_totale=imponibile,
        aliquota_iva=VAT_RATE,
    )
    body.dati_beni_servizi.build_dati_riepilogo()
    body.build_importo_totale_documento()
    return _render(fattura)
