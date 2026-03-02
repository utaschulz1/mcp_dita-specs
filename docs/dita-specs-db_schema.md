-- WARNING: This schema is for context only and is not meant to be run.
-- Table order and constraints may not be valid for execution.

CREATE TABLE public.code_examples (
  id bigint NOT NULL DEFAULT nextval('code_examples_id_seq'::regclass),
  url character varying NOT NULL,
  chunk_number integer NOT NULL,
  content text NOT NULL,
  summary text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  source_id text NOT NULL,
  embedding USER-DEFINED,
  created_at timestamp with time zone NOT NULL DEFAULT timezone('utc'::text, now()),
  CONSTRAINT code_examples_pkey PRIMARY KEY (id),
  CONSTRAINT code_examples_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.sources(source_id)
);
CREATE TABLE public.crawled_pages (
  id bigint NOT NULL DEFAULT nextval('crawled_pages_id_seq'::regclass),
  url character varying NOT NULL,
  chunk_number integer NOT NULL,
  content text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  source_id text NOT NULL,
  embedding USER-DEFINED,
  created_at timestamp with time zone NOT NULL DEFAULT timezone('utc'::text, now()),
  CONSTRAINT crawled_pages_pkey PRIMARY KEY (id),
  CONSTRAINT crawled_pages_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.sources(source_id)
);
CREATE TABLE public.sources (
  source_id text NOT NULL,
  summary text,
  total_word_count integer DEFAULT 0,
  created_at timestamp with time zone NOT NULL DEFAULT timezone('utc'::text, now()),
  updated_at timestamp with time zone NOT NULL DEFAULT timezone('utc'::text, now()),
  CONSTRAINT sources_pkey PRIMARY KEY (source_id)
);