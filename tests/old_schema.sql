-- Schema of the old system's database (no data): pg_restore --schema-only --no-owner --no-privileges of the
-- pre-refactor production backup DB_backups/wifitest_20261020_145811.dump. tests/db_compat.sh uses it to prove that a
-- backup this project already upgraded, with rollback_web_additions.sql applied, is exactly the old schema.
--
-- PostgreSQL database dump
--


-- Dumped from database version 16.13
-- Dumped by pg_dump version 16.13

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: test_record; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.test_record (
    id integer NOT NULL,
    product_model text,
    work_order text,
    sn text NOT NULL,
    wifi_mac text,
    bt_mac text,
    test_time timestamp without time zone NOT NULL,
    result boolean NOT NULL,
    avg_24g double precision,
    min_24g double precision,
    max_24g double precision,
    std_24g double precision,
    samples_24g integer,
    rssi_before_24g double precision,
    rssi_after_24g double precision,
    rssi_delta_24g double precision,
    attempt_count_24g integer DEFAULT 1,
    band_result_24g boolean,
    avg_5g double precision,
    min_5g double precision,
    max_5g double precision,
    std_5g double precision,
    samples_5g integer,
    rssi_before_5g double precision,
    rssi_after_5g double precision,
    rssi_delta_5g double precision,
    attempt_count_5g integer DEFAULT 1,
    band_result_5g boolean,
    bt_avg_latency double precision,
    bt_min_latency double precision,
    bt_max_latency double precision,
    bt_loss_rate double precision,
    bt_result boolean,
    raw_log text,
    file_hash text,
    source_file text,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: test_record_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.test_record_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: test_record_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.test_record_id_seq OWNED BY public.test_record.id;


--
-- Name: v_fail_detail; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_fail_detail AS
 SELECT test_time,
    sn,
    work_order,
    product_model,
    band_result_24g,
    band_result_5g,
    bt_result,
    avg_24g,
    avg_5g,
    rssi_before_24g,
    rssi_after_24g,
    rssi_delta_24g,
    rssi_before_5g,
    rssi_after_5g,
    rssi_delta_5g,
    bt_avg_latency,
    bt_loss_rate
   FROM public.test_record
  WHERE (result = false)
  ORDER BY test_time DESC;


--
-- Name: v_unstable_units; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_unstable_units AS
 SELECT test_time,
    sn,
    work_order,
    std_5g,
    avg_5g,
    rssi_delta_5g,
    attempt_count_5g
   FROM public.test_record
  WHERE ((std_5g > (5)::double precision) OR (rssi_delta_5g < ('-20'::integer)::double precision))
  ORDER BY test_time DESC;


--
-- Name: v_yield_by_wo; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_yield_by_wo AS
 SELECT work_order,
    product_model,
    count(*) AS total,
    count(*) FILTER (WHERE (result = true)) AS passed,
    count(*) FILTER (WHERE (result = false)) AS failed,
    round((((count(*) FILTER (WHERE (result = true)))::numeric * 100.0) / (count(*))::numeric), 2) AS yield_pct,
    round((avg(avg_24g))::numeric, 1) AS avg_24g,
    round((avg(avg_5g))::numeric, 1) AS avg_5g,
    round((((count(*) FILTER (WHERE ((attempt_count_24g > 1) OR (attempt_count_5g > 1))))::numeric * 100.0) / (count(*))::numeric), 2) AS retry_rate_pct,
    min(test_time) AS first_test,
    max(test_time) AS last_test
   FROM public.test_record
  GROUP BY work_order, product_model;


--
-- Name: test_record id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_record ALTER COLUMN id SET DEFAULT nextval('public.test_record_id_seq'::regclass);


--
-- Name: test_record test_record_file_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_record
    ADD CONSTRAINT test_record_file_hash_key UNIQUE (file_hash);


--
-- Name: test_record test_record_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.test_record
    ADD CONSTRAINT test_record_pkey PRIMARY KEY (id);


--
-- Name: idx_model; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_model ON public.test_record USING btree (product_model);


--
-- Name: idx_model_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_model_time ON public.test_record USING btree (product_model, test_time);


--
-- Name: idx_result; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_result ON public.test_record USING btree (result);


--
-- Name: idx_sn; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sn ON public.test_record USING btree (sn);


--
-- Name: idx_test_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_test_time ON public.test_record USING btree (test_time);


--
-- Name: idx_wo_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wo_time ON public.test_record USING btree (work_order, test_time);


--
-- Name: idx_work_order; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_work_order ON public.test_record USING btree (work_order);


--
-- PostgreSQL database dump complete
--


