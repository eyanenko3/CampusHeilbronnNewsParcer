-- =============================================================================
-- seed_sample_data.sql
-- -----------------------------------------------------------------------------
-- Populates the database through the exact same ingest.* SQL functions that the
-- Python parsers call in production, seeding both `tum_chn_news` (News &
-- Stories) and `tum_chn_events` (Campus Events).
--
-- Usage:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f database/scripts/seed_sample_data.sql
-- =============================================================================

DO $$
DECLARE
    v_news_run_id   bigint;
    v_events_run_id bigint;
BEGIN
    -- 1. Seed TUM Campus Heilbronn News (`tum_chn_news`) ----------------------
    v_news_run_id := ingest.start_run(
        'tum_chn_news',
        'TUM Campus Heilbronn – News & Stories',
        'https://chn.tum.de/stories/news'
    );

    PERFORM ingest.upsert_event(
        v_news_run_id,
        jsonb_build_object(
            'external_id', 'tx-news-1022',
            'title',       'Edge AI in Cars',
            'url',         'https://chn.tum.de/stories/mindshift-online/mindshift-article/edge-ai-in-cars',
            'starts_at',   '2026-11-05T12:50:51+01:00',
            'all_day',     false,
            'summary',     'How can artificial intelligence (AI) process data directly in a car? Sreelakshmi Rameshan, a doctoral researcher at TUM Campus Heilbronn, is working with Audi to explore how AI models can run quickly, reliably, and energy-efficiently on vehicle hardware.',
            'description', 'How can artificial intelligence (AI) process data directly in a car? Sreelakshmi Rameshan, a doctoral researcher at TUM Campus Heilbronn, is working with Audi to explore how AI models can run quickly, reliably, and energy-efficiently on vehicle hardware. One application is using AI to help emergency responders act faster after a crash.',
            'image_url',   'https://chn.tum.de/fileadmin/chn.tum.de/Stories/Mindshift_Online/Profs_und_Mitarbeitende/tum-campus-heilbronn-magazinbeitrag-Sreelakshmi-Rameshan.png',
            'organizer',   'TUM Campus Heilbronn',
            'language',    'en',
            'tags',        jsonb_build_array('Magazine Article', 'AI Competence', 'Research'),
            'extra',       jsonb_build_object('kind', 'news')
        )
    );

    PERFORM ingest.upsert_event(
        v_news_run_id,
        jsonb_build_object(
            'external_id', 'tx-news-1020',
            'title',       'HDSC Connects Munich and Heilbronn',
            'url',         'https://chn.tum.de/stories/mindshift-online/mindshift-article/hdsc-connects-munich-and-heilbronn',
            'starts_at',   '2026-11-04T10:00:00+01:00',
            'all_day',     false,
            'summary',     'The Heilbronn Data Science Center (HDSC) introduced itself at TUM Heilbronn Campus with a scientific symposium focusing on AI collaboration between Munich and Heilbronn.',
            'description', 'The Heilbronn Data Science Center (HDSC) introduced itself at TUM Heilbronn Campus with a scientific symposium. The event focused on collaboration between researchers from Munich and Heilbronn, current projects in artificial intelligence, and the transfer of research into practice.',
            'image_url',   'https://chn.tum.de/fileadmin/_processed_/6/e/csm_tum-campus-heilbronn-news-hdsc-symposium_81bf9f66c1.webp',
            'organizer',   'TUM Campus Heilbronn',
            'language',    'en',
            'tags',        jsonb_build_array('News', 'AI Competence'),
            'extra',       jsonb_build_object('kind', 'news')
        )
    );

    PERFORM ingest.finish_run(v_news_run_id, 'succeeded', 0, NULL, true);

    -- 2. Seed TUM Campus Heilbronn Events (`tum_chn_events`) ------------------
    v_events_run_id := ingest.start_run(
        'tum_chn_events',
        'TUM Campus Heilbronn – Events',
        'https://chn.tum.de/events'
    );

    PERFORM ingest.upsert_event(
        v_events_run_id,
        jsonb_build_object(
            'external_id',      'calendarize-event-885',
            'title',            'Exhibition Thomas Mann – In the Burning Landscape of Democracy and AI TO EYE',
            'url',              'https://chn.tum.de/event/event/exhibition-thomas-mann',
            'starts_at',        '2026-11-06T11:30:00+01:00',
            'ends_at',          '2026-12-10T14:00:00+01:00',
            'all_day',          false,
            'summary',          'A joint exhibition by TUM and ETH Zurich at the Bildungscampus Heilbronn (06.11.2026 – 02.12.2026)',
            'description',      'For the first time, the Technical University of Munich and ETH Zurich are jointly presenting the exhibitions Thomas Mann – In the Burning Landscape of Democracy and AI TO EYE at the Bildungscampus Heilbronn.',
            'image_url',        'https://chn.tum.de/fileadmin/_processed_/7/5/csm_tum-campus-heilbronn-thomas-mann_9a49d98f2a.jpg',
            'registration_url', 'https://chn.tum.de/event/event/exhibition-thomas-mann',
            'location',         'Bildungscampus Heilbronn, hip-Turm, Edisonstraße 15, Heilbronn',
            'organizer',        'A joint exhibition by TUM and ETH Zurich at the Bildungscampus Heilbronn',
            'language',         'en',
            'tags',             jsonb_build_array('General Public', 'Campus'),
            'extra',            jsonb_build_object(
                                    'ics_url', 'https://chn.tum.de/event/event/exhibition-thomas-mann?tx_calendarize_calendar%5Bformat%5D=ics&type=1600',
                                    'registration_form_on_page', true
                                )
        )
    );

    PERFORM ingest.upsert_event(
        v_events_run_id,
        jsonb_build_object(
            'external_id',      'calendarize-event-517',
            'title',            'Bürger-Uni: Computing for Health and Climate',
            'url',              'https://chn.tum.de/event/event/buerger-uni-11112026',
            'starts_at',        '2026-11-11T00:00:00+01:00',
            'ends_at',          '2026-11-11T23:59:59+01:00',
            'all_day',          true,
            'summary',          'Prof. Dr. Hartwig Anzt talks about Computing for Health and Climate at the Bildungscampus.',
            'description',      'Since 2015 the Bürger-Uni welcomes interested people from the region at the Bildungscampus to participate in lectures about the latest research results.',
            'image_url',        'https://chn.tum.de/fileadmin/_processed_/c/4/csm_tum-campus-heilbronn-event-header-buerger-uni_77c4306796.webp',
            'registration_url', 'https://chn.tum.de/event/event/buerger-uni-11112026',
            'location',         'AULA Bildungscampus, Bildungscampus 6, 74076 Heilbronn',
            'organizer',        'TUM Campus Heilbronn',
            'language',         'en',
            'tags',             jsonb_build_array('General Public', 'Current Students'),
            'extra',            '{}'::jsonb
        )
    );

    PERFORM ingest.finish_run(v_events_run_id, 'succeeded', 0, NULL, true);
END;
$$;
