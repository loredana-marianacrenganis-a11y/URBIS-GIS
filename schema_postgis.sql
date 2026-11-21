-- URBIS GIS – modelul conceptual al bazei de date spațio-temporale (livrabil WP1)
-- PostgreSQL 16 + PostGIS. Aceeași structură e implementată portabil în SQLite (db.py).
-- Principiu: nicio înregistrare nu se suprascrie; fiecare modificare închide versiunea
-- veche (valid_pana_la) și deschide una nouă (valid_de_la), cu sursa și evenimentul ei.
-- Sistem de coordonate: Stereo 70 (EPSG:3844).

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;          -- pgvector, pentru indexul RAG

CREATE TABLE sursa (
  id            serial PRIMARY KEY,
  nume          text NOT NULL,
  tip           text NOT NULL CHECK (tip IN ('OSM','fisier','ortofoto','ANCPI','primarie','masuratori','mapathon','manual')),
  data_import   timestamptz NOT NULL DEFAULT now(),
  an_referinta  integer,
  precizie      text,
  licenta       text,
  detalii       text
);

CREATE TABLE zona (
  id          serial PRIMARY KEY,
  nume        text NOT NULL,
  tip         text NOT NULL,                     -- pilot, UTR, cartier, zonă de studiu
  cod_siruta  text,
  geom        geometry(MultiPolygon, 3844) NOT NULL
);

CREATE TABLE imobil (
  id                  serial PRIMARY KEY,
  cod_imobil          text NOT NULL,             -- identitate stabilă între versiuni
  nr_cadastral        text,
  nr_cf               text,
  suprafata_acte      numeric,
  suprafata_masurata  numeric,
  categorie_folosinta text,
  intravilan          boolean,
  geom                geometry(MultiPolygon, 3844) NOT NULL,
  valid_de_la         integer NOT NULL,
  valid_pana_la       integer,
  sursa_id            integer REFERENCES sursa(id),
  CHECK (valid_pana_la IS NULL OR valid_pana_la > valid_de_la)
);

CREATE TABLE titular (                           -- date personale minimizate (GDPR)
  id          serial PRIMARY KEY,
  cod_imobil  text NOT NULL,
  tip         text CHECK (tip IN ('fizic','juridic')),
  cota_parte  text
);

CREATE TABLE constructie (
  id               serial PRIMARY KEY,
  cod_constructie  text NOT NULL,                -- identitate stabilă între versiuni
  cod_imobil       text,
  tip              text,
  niveluri         integer,
  niveluri_sursa   text,                         -- OSM, fișier, estimat, mapathon
  an_constructie   integer,
  adresa           text,
  nume             text,
  osm_id           text,
  amprenta_m2      numeric,
  geom             geometry(MultiPolygon, 3844) NOT NULL,
  valid_de_la      integer NOT NULL,
  valid_pana_la    integer,
  sursa_id         integer REFERENCES sursa(id),
  CHECK (valid_pana_la IS NULL OR valid_pana_la > valid_de_la)
);

CREATE TABLE document (
  id               serial PRIMARY KEY,
  cod_imobil       text,
  cod_constructie  text,
  tip              text,                         -- CF, plan de amplasament, AC, PUZ/PUG, normativ
  titlu            text,
  data             date,
  fisier           text,
  text             text,
  date_personale   boolean NOT NULL DEFAULT false   -- true: nu intră în indexul RAG (GDPR)
);

CREATE TABLE fragment_document (                 -- index RAG
  id           serial PRIMARY KEY,
  document_id  integer REFERENCES document(id) ON DELETE CASCADE,
  text         text NOT NULL,
  embedding    vector(384)                       -- paraphrase-multilingual-MiniLM-L12-v2
);

CREATE TABLE lucrare (
  id          serial PRIMARY KEY,
  cod_imobil  text,
  tip         text,
  client      text,
  data        date,
  stare       text,
  operator    text
);

CREATE TABLE eveniment (
  id          serial PRIMARY KEY,
  ts          timestamptz NOT NULL DEFAULT now(),
  entitate    text NOT NULL,                     -- constructie, imobil
  cod         text NOT NULL,
  tip         text NOT NULL,                     -- inventar, creare, extindere, modificare, demolare, validare, corectare
  an          integer,
  sursa_id    integer REFERENCES sursa(id),
  detalii     text,
  utilizator  text
);

CREATE TABLE observatie (                        -- validare prin mapathon (M7)
  id                serial PRIMARY KEY,
  ts                timestamptz NOT NULL DEFAULT now(),
  cod_constructie   text,
  autor             text,
  niveluri_propuse  integer,
  tip_propus        text,
  exista            boolean,
  comentariu        text,
  stare             text NOT NULL DEFAULT 'nouă' CHECK (stare IN ('nouă','acceptată','respinsă')),
  geom              geometry(Point, 3844),
  rezolvat_de       text,
  rezolvat_la       timestamptz
);

CREATE TABLE indicator (
  id           serial PRIMARY KEY,
  zona_id      integer REFERENCES zona(id),
  tip          text,                             -- POT, CUT, densitate, construcții noi
  perioada     text,
  valoare      numeric,
  calculat_la  timestamptz DEFAULT now()
);

CREATE TABLE referinta (                         -- valori de referință pentru raportul de impact
  id          serial PRIMARY KEY,
  an          integer NOT NULL,
  indicator   text NOT NULL,
  valoare     numeric,
  unitate     text,
  observatii  text,
  ts          timestamptz DEFAULT now(),
  UNIQUE (an, indicator)
);

CREATE TABLE jurnal (                            -- M8, date pentru raportul de impact
  id         serial PRIMARY KEY,
  ts         timestamptz NOT NULL DEFAULT now(),
  actiune    text NOT NULL,
  rol        text,
  durata_ms  integer,
  detalii    jsonb
);

CREATE INDEX ON constructie USING gist (geom);
CREATE INDEX ON constructie (cod_constructie);
CREATE INDEX ON constructie (valid_de_la, valid_pana_la);
CREATE INDEX ON imobil USING gist (geom);
CREATE INDEX ON imobil (nr_cadastral);
CREATE INDEX ON zona USING gist (geom);
CREATE INDEX ON eveniment (cod);

-- Starea fondului construit la un an dat (exemplu: 2024)
-- SELECT * FROM constructie WHERE valid_de_la <= 2024 AND (valid_pana_la IS NULL OR valid_pana_la > 2024);
