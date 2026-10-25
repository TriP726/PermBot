import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.music import (
    Music,
    TrackChoiceSelect,
    TrackChoiceView,
    match_score,
    online_entry_url,
)


class MusicChoiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.music = Music(None)
        self.requester = SimpleNamespace(id=1234, display_name="Test User")

    def test_local_matches_are_shown_without_online_results(self):
        local_matches = [
            {
                "path": "music/Believer.mp3",
                "title": "Believer",
                "artist": "Imagine Dragons",
                "duration": 204,
            },
            {
                "path": "music/Believer - Live.flac",
                "title": "Believer (Live)",
                "artist": "Imagine Dragons",
                "duration": 225,
            },
        ]
        online_matches = [
            {
                "id": "official123",
                "url": "official123",
                "title": "Believer (Official Music Video)",
                "channel": "Imagine Dragons",
                "duration": 214,
            },
            {
                "webpage_url": "https://www.youtube.com/watch?v=lyrics123",
                "title": "Believer - Lyrics",
                "uploader": "Lyrics Channel",
                "duration": 210,
            },
        ]

        choices = self.music.build_track_choices("Believer", local_matches, online_matches)

        self.assertEqual(len(choices), 2)
        self.assertEqual([choice.title for choice in choices], ["Believer", "Believer (Live)"])
        self.assertTrue(all(choice.is_local for choice in choices))

    async def test_ensure_voice_supports_library_cogs_call_signature(self):
        voice = SimpleNamespace(channel=object())
        target_channel = SimpleNamespace(connect=AsyncMock(return_value=voice))
        member = SimpleNamespace(voice=SimpleNamespace(channel=target_channel))
        guild = SimpleNamespace(voice_client=None)

        connected = await self.music.ensure_voice(guild, member)

        self.assertIs(connected, voice)
        target_channel.connect.assert_awaited_once_with()

    def test_online_fallback_includes_youtube_and_soundcloud(self):
        choices = self.music.build_track_choices(
            "Believer",
            [],
            [
                {
                    "id": "official123",
                    "url": "official123",
                    "title": "Believer (Official Music Video)",
                    "channel": "Imagine Dragons",
                    "duration": 214,
                    "_search_source": "ytsearch",
                },
                {
                    "webpage_url": "https://soundcloud.com/example/believer",
                    "title": "Believer - Remix",
                    "artist": "Example Artist",
                    "duration": 210,
                    "_search_source": "scsearch",
                },
            ],
        )

        self.assertEqual(len(choices), 2)
        self.assertEqual(choices[0].source_label, "🌐 YouTube")
        self.assertEqual(choices[0].webpage_url, "https://www.youtube.com/watch?v=official123")
        self.assertEqual(choices[1].source_label, "🌐 SoundCloud")

    async def test_local_results_skip_youtube_and_soundcloud_searches(self):
        local = [{"path": "music/Believer.mp3", "title": "Believer", "artist": "Imagine Dragons"}]
        with patch.object(Music, "_search_local_matches", return_value=local), patch.object(
            Music, "_search_online_entries"
        ) as online_search:
            choices = await self.music.find_track_choices("Believer")

        self.assertEqual([choice.title for choice in choices], ["Believer"])
        self.assertTrue(choices[0].is_local)
        online_search.assert_not_called()

    async def test_no_local_results_search_both_online_services(self):
        youtube = [{
            "id": "video123",
            "title": "Example video",
            "_search_source": "ytsearch",
        }]
        soundcloud = [{
            "webpage_url": "https://soundcloud.com/artist/example",
            "title": "Example track",
            "_search_source": "scsearch",
        }]

        def search_online(query, source):
            return youtube if source == "ytsearch" else soundcloud

        with patch.object(Music, "_search_local_matches", return_value=[]), patch.object(
            Music, "_search_online_entries", side_effect=search_online
        ) as online_search:
            choices = await self.music.find_track_choices("Example")

        self.assertEqual(online_search.call_count, 2)
        self.assertEqual([choice.source_label for choice in choices], ["🌐 YouTube", "🌐 SoundCloud"])

    def test_search_menu_respects_discord_component_limits(self):
        local_matches = [
            {
                "path": f"music/Similar Song {index}.mp3",
                "title": f"Similar Song version {index}",
                "artist": "Example Artist",
                "duration": 180 + index,
            }
            for index in range(30)
        ]
        online_matches = [
            {
                "id": f"video{index}",
                "title": f"Similar Song online version {index}",
                "channel": "Example Artist",
                "duration": 180 + index,
            }
            for index in range(30)
        ]
        choices = self.music.build_track_choices("Similar Song", local_matches, online_matches)
        view = TrackChoiceView(self.music, self.requester.id, choices)
        select = next(child for child in view.children if isinstance(child, TrackChoiceSelect))

        self.assertLessEqual(len(select.options), 25)
        self.assertEqual(len(select.options), len(choices))
        self.assertTrue(all(len(option.label) <= 100 for option in select.options))
        self.assertTrue(all(len(option.description or "") <= 100 for option in select.options))

    async def test_local_choice_builds_a_playable_local_song(self):
        choice = self.music.build_track_choices(
            "Blue Monday",
            [{"path": "music/Blue Monday.mp3", "title": "Blue Monday", "artist": "New Order", "duration": 440}],
            [],
        )[0]

        song = await self.music.build_song_from_choice(choice, self.requester)
        self.assertEqual(song.title, "Blue Monday")
        self.assertEqual(song.local_path, "music/Blue Monday.mp3")
        self.assertTrue(song.is_local)

    def test_online_results_require_a_playable_stream_url_and_use_choice_metadata(self):
        choice = self.music.build_track_choices(
            "Example",
            [],
            [{"id": "example123", "title": "Example Song", "channel": "Example Artist"}],
        )[0]
        self.assertEqual(choice.webpage_url, "https://www.youtube.com/watch?v=example123")

        song = self.music._song_from_extracted_data(
            {"url": "https://audio.example/stream", "webpage_url": choice.webpage_url},
            self.requester,
            choice,
        )
        self.assertEqual(song.title, "Example Song")
        self.assertEqual(song.artist, "Example Artist")
        self.assertEqual(song.requester, self.requester)

    def test_fuzzy_matching_prefers_similar_titles_and_url_fallback(self):
        self.assertGreater(match_score("Believer", "Beliver"), 0.8)
        self.assertGreater(match_score("Shape of You", "Ed Sheeran - Shape of You (Official Video)"), 0.8)
        self.assertLess(match_score("Believer", "A completely unrelated song"), 0.5)
        self.assertEqual(
            online_entry_url({"id": "fallback123", "url": "fallback123"}),
            "https://www.youtube.com/watch?v=fallback123",
        )


if __name__ == "__main__":
    unittest.main()
