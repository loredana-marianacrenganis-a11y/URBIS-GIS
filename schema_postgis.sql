CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;

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
  tip         text NOT NULL,
  cod_siruta  text,
  geom        geometry(MultiPolygon, 3844) NOT NULL
);

CREATE TABLE imobil (
  id                  serial PRIMARY KEY,
  cod_imobil          text NOT NULL,
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

CREATE TABLE titular (
  id          serial PRIMARY KEY,
  cod_imobil  text NOT NULL,
  tip         text CHECK (tip IN ('fizic','juridic')),
  cota_parte  text
);

CREATE TABLE constructie (
  id               serial PRIMARY KEY,
  cod_constructie  text NOT NULL,
  cod_imobil       text,
  tip              text,
  niveluri         integer,
  niveluri_sursa   text,
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
  tip              text,
  titlu            text,
  data             date,
  fisier           text,
  text             text,
  date_personale   boolean NOT NULL DEFAULT false
);

CREATE TABLE fragment_document (
  id           serial PRIMARY KEY,
  document_id  integer REFERENCES document(id) ON DELETE CASCADE,
  text         text NOT NULL,
  embedding    vector(384)
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
  entitate    text NOT NULL,
  cod         text NOT NULL,
  tip         text NOT NULL,
  an          integer,
  sursa_id    integer REFERENCES sursa(id),
  detalii     text,
  utilizator  text
);

CREATE TABLE observatie (
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
  tip          text,
  perioada     text,
  valoare      numeric,
  calculat_la  timestamptz DEFAULT now()
);

CREATE TABLE referinta (
  id          serial PRIMARY KEY,
  an          integer NOT NULL,
  indicator   text NOT NULL,
  valoare     numeric,
  unitate     text,
  observatii  text,
  ts          timestamptz DEFAULT now(),
  UNIQUE (an, indicator)
);

CREATE TABLE jurnal (
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
