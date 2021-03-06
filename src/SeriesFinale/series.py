# -*- coding: utf-8 -*-

###########################################################################
#    SeriesFinale
#    Copyright (C) 2009 Joaquim Rocha <jrocha@igalia.com>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
###########################################################################

import os
import pyotherside
from SeriesFinale.lib import thetvdbapi, serializer, constants
from SeriesFinale.lib.util import image_downloader
from SeriesFinale.lib.listmodel import *
from xml.etree import ElementTree as ET
from SeriesFinale.asyncworker import AsyncWorker, AsyncItem
from SeriesFinale.lib.constants import TVDB_API_KEY, DATA_DIR, DEFAULT_LANGUAGES, SF_LANG_FILE
from SeriesFinale.settings import Settings
from datetime import datetime
from datetime import timedelta
from xml.sax import saxutils
import gettext
import locale
import logging
logging.basicConfig(level=logging.DEBUG)

_ = gettext.gettext

class Show(object):

    def __init__(self, name, genre = None, overview = None, network = None,
                 rating = None, actors = [], episode_list = [], image = None,
                 thetvdb_id = -1, season_images = {}, id = -1, language = None,
                 status = None, airs_time = '', runtime = None, imdb_id = None,
                 downloading_show_image = False, downloading_season_image = False):
        self.id = id
        self.name = name
        self.genre = genre
        self.overview = overview
        self.network = network
        self.rating = rating
        self.actors = actors
        self.episode_list = episode_list
        self.image = image
        self.season_images = season_images
        self.thetvdb_id = thetvdb_id
        self.imdb_id = imdb_id
        self.language = language
        self.status = status
        self.runtime = runtime if not runtime=='None' else None
        try:
            self.airs_time = datetime.strptime(airs_time, '%H:%M').time()
        except:
            self.airs_time = ''
        self.downloading_show_image = False
        self.downloading_season_image = downloading_season_image
        self._updating = False

    def get_dict(self):
        return {'showName': self.get_name(),
                'showOverview': self.get_overview(),
                'showGenre': self.genre[0] if self.genre else 'Other',
                'showNetwork': self.network,
                'runtime': int(self.runtime) if self.runtime else 60,
                'airsTime': self.airs_time.strftime('%H:%M') if self.airs_time else '',
                'infoMarkup': self.get_info_markup(),
                'coverImage': self.cover_image(),
                'lastAired': self.get_most_recent_air_date(),
                'nextToWatch': self.get_next_unwatched_air_date(),
                'isWatched': self.is_completely_watched(only_aired = True),
                'isUpdating': self.get_updating(),
                'imdbId': self.imdb_id if self.imdb_id else '',
                'nextIsPremiere' : self.next_is_premiere(),
                'isShowPremiere' : self.next_is_show_premiere()}

    def get_updating(self): return self._updating
    def set_updating(self, updating):
        self._updating = updating
        pyotherside.send('showUpdating', self._updating)

    def cover_image(self):
        try:
            if os.path.exists(self.image):
                return self.image
        except:
            pass
        return constants.PLACEHOLDER_IMAGE
    def set_cover_image(self, new_path):
        self.image = new_path
        pyotherside.send('coverImageChanged', self.get_name(), self.image)

    def get_name(self):
        return self.name
    def set_name(self, name):
        self.name = name
        pyotherside.send('nameChanged', self.name)

    def get_overview(self):
        return self.overview
    def set_overview(self, overview):
        self.overview = overview
        pyotherside.send('overviewChanged', self.overview)

    def get_episodes_by_season(self, season_number):
        if season_number is None:
            return self.episode_list
        return [episode for episode in self.episode_list if episode.season_number == season_number]

    def get_next_episode(self, episode):
        return self._get_episode_by_offset(episode, 1)

    def get_previous_episode(self, episode):
        return self._get_episode_by_offset(episode, -1)

    def _get_episode_by_offset(self, episode, offset):
        episodes = [(ep.episode_number, ep) for ep in \
                    self.get_episodes_by_season(episode.season_number)]
        episodes.sort()
        number_of_episodes = len(episodes)
        for i in range(number_of_episodes):
            current_episode = episodes[i][1]
            if current_episode == episode:
                return episodes[(i + offset) % number_of_episodes][1]
        return episode

    def _get_episode_by_name(self, episode_name):
        for episode in self.episode_list:
            if episode.get_title() == episode_name:
                return episode
        return None

    def get_seasons_model(self):
        season_list = []
        for season in self.get_seasons():
            season_list.append({'seasonName': self.get_season_name(season),
                                'seasonNumber': season,
                                'seasonInfoMarkup': self.get_season_info_markup(season),
                                'seasonImage': self.get_season_image(season),
                                'isWatched': self.is_completely_watched(season, only_aired = True)})
        return SortedSeasonsList(season_list, Settings())

    def get_seasons(self):
        seasons = []
        for episode in self.episode_list:
            season_number = episode.season_number
            if season_number not in seasons:
                seasons.append(season_number)
        return seasons

    def get_season_image(self, season):
        retval = constants.PLACEHOLDER_IMAGE
        if (season not in self.season_images):
            return retval
        if os.path.exists(self.season_images[season]):
            return self.season_images[season]
        return retval

    def get_episode_list_by_season(self, season):
        return [episode for episode in self.episode_list \
                  if episode.season_number == season]

    def get_sorted_episode_list_by_season(self, season):
        episode_list = []
        for episode in self.get_episode_list_by_season(season):
            episode_list.append(episode.get_dict())
        return SortedEpisodesList(episode_list, Settings())

    def update_episode_list(self, episode_list):
        add_special_seasons = Settings().getConf(Settings.ADD_SPECIAL_SEASONS)
        series_manager = SeriesManager()
        for episode in episode_list:
            exists = False
            for ep in self.episode_list:
                if ep == episode:
                    exists = True
                    ep.merge_episode(episode)
                    series_manager.changed = True
            if not exists:
                if not add_special_seasons and \
                   self.is_special_season(episode.season_number):
                    continue
                self.episode_list.append(episode)
                series_manager.changed = True

    def delete_episode(self, episode):
        series_manager = SeriesManager()
        for i in range(len(self.episode_list)):
            if self.episode_list[i] == episode:
                del self.episode_list[i]
                series_manager.changed = True
                break

    def delete_season(self, season):
        episodes = self.get_episode_list_by_season(season)
        for episode in episodes:
            self.delete_episode(episode)
        pyotherside.send('infoMarkupChanged')

    def mark_all_episodes_as_not_watched(self, season = None):
        self._mark_all_episodes(False, season)

    def mark_all_episodes_as_watched(self, season = None):
        self._mark_all_episodes(True, season)

    def _mark_all_episodes(self, watched, season = None):
        episodes = self.get_episodes_by_season(season)
        for episode in episodes:
            if episode.already_aired():
                episode.watched = watched
        SeriesManager().updated()
        pyotherside.send('infoMarkupChanged')

    def is_completely_watched(self, season = None, only_aired = False):
        episodes = self.get_episodes_by_season(season)
        for episode in episodes:
            if not only_aired or episode.already_aired():
                if not episode.watched:
                    return False
        return True

    def next_is_premiere(self, season = None):
        episodes_info = self.get_episodes_info(season)
        next_episode = episodes_info['next_episode']
        if next_episode and next_episode.episode_number == 1:
            return True
        return False

    def next_is_show_premiere(self, season = None):
        episodes_info = self.get_episodes_info(season)
        next_episode = episodes_info['next_episode']
        if next_episode and next_episode.episode_number == 1 and next_episode.season_number == '1':
            return True
        return False



    def get_episodes_info(self, season = None):
        info = {}
        if season:
            episodes = self.get_episode_list_by_season(season)
        else:
            episodes = self.episode_list
        episodes_to_watch = [episode for episode in episodes \
                            if not episode.watched]
        info['episodes'] = episodes
        #Filter out only the aired episodes
        info['episodes_to_watch'] = [episode for episode in episodes_to_watch \
                            if episode.already_aired()]
        if season:
            sorted_episodes_to_watch = [('%02d'%int(episode.season_number), episode.episode_number, episode) \
                                         for episode in episodes_to_watch if episode.air_date]
        else:
            # Leave Extras for end
            sorted_episodes_to_watch = [('%02d'%int(episode.season_number), episode.episode_number, episode) \
                                         for episode in episodes_to_watch \
                                         if episode.season_number != '0' and episode.air_date]
            if not sorted_episodes_to_watch:
                sorted_episodes_to_watch = [('%02d'%int(episode.season_number), episode.episode_number, episode) \
                                             for episode in episodes_to_watch \
                                             if episode.season_number == '0' and episode.air_date]
        sorted_episodes_to_watch.sort()
        if sorted_episodes_to_watch:
            info['next_episode'] = sorted_episodes_to_watch[0][2]
        else:
            info['next_episode'] = None
        return info

    def __str__(self):
        return self.name

    def get_info_markup(self, info = None):
        seasons = len(self.get_seasons())
        if seasons:
            episodes_info = info or self.get_episodes_info()
            episodes_to_watch = episodes_info['episodes_to_watch']
            next_episode = episodes_info['next_episode']
            show_info = gettext.ngettext('%s season', '%s seasons', seasons) \
                         % seasons
            if self.is_completely_watched():
                show_info += '<br/>' + _('Completely watched')
                if self.status and self.status == 'Ended':
                    show_info += '<br/>' + _('Show has ended')
            else:
                if episodes_to_watch:
                    n_episodes_to_watch = len(episodes_to_watch)
                    show_info += '<br/>' + gettext.ngettext('%s episode not watched',
                                                          '%s episodes not watched',
                                                          n_episodes_to_watch) \
                                                          % n_episodes_to_watch
                else:
                    show_info += '<br/>' + _('No episodes to watch')
                if next_episode:
                    next_air_date = next_episode.air_date
                    if next_air_date:
                        show_info += '<br/>' + _('<i>Next:</i> %s, %s') % \
                                     (next_episode.get_episode_show_number(), \
                                     next_episode.get_air_date_text())
                    else:
                        show_info += '<br/>' + _('<i>Next:</i> %s') % \
                                     next_episode.get_episode_show_number()
        else:
            show_info = ''
        return show_info

    def get_season_name(self, season):
        if season == '0':
            return _('Special')
        else:
            return _('Season %s') % season

    def get_season_info_markup(self, season):
        info = self.get_episodes_info(season)
        episodes = info['episodes']
        episodes_to_watch = info['episodes_to_watch']
        next_episode = info['next_episode']
        season_info = ''
        if self.is_completely_watched(season):
            season_info = _('Completely watched')
        else:
            if episodes_to_watch:
                n_episodes_to_watch = len(episodes_to_watch)
                season_info = gettext.ngettext('%s episode not watched',
                                           '%s episodes not watched',
                                           n_episodes_to_watch) \
                                           % n_episodes_to_watch
            else:
                season_info = _('No episodes to watch')
            if next_episode:
                next_air_date = next_episode.air_date
                if next_air_date:
                    season_info += '<br/>' + _('<i>Next episode:</i> %s, %s') % \
                                   (next_episode.episode_number, \
                                    next_episode.get_air_date_text())
                else:
                    season_info += '<br/>' + _('<i>Next episode:</i> %s') % \
                                   next_episode.episode_number
        return season_info

    def get_most_recent_air_date(self):
        episodes = self.episode_list
        aired_episodes = [episode for episode in episodes \
                            if episode.already_aired()]
        try:
            sorted_list = sorted(aired_episodes, key=lambda k: k.air_date)
            return sorted_list[-1].air_date
        except:
            return None

    def get_next_unwatched_air_date(self):
        episodes_info = self.get_episodes_info()
        next_episode = episodes_info['next_episode']
        try:
            return next_episode.air_date
        except:
            return None


    def get_poster_prefix(self):
        if self.thetvdb_id != -1:
            return str(self.thetvdb_id)
        return str(self.name)

    def get_season_poster_prefix(self, season = None):
        prefix = '%s_season_' % self.get_poster_prefix()
        if season:
            prefix = '%s%s' % (prefix, str(season))
        return prefix

    def assign_image_to_season(self, image):
        basename = os.path.basename(image)
        prefix, extension = os.path.splitext(basename)
        for season in self.get_seasons():
            if prefix.endswith(season) and not self.season_images.get(season):
                self.season_images[season] = image
                break

    def is_special_season(self, season_number):
        try:
            season_number = int(season_number)
        except ValueError:
            return False
        return season_number == 0

class Episode(object):

    def __init__(self, name, show, episode_number, season_number = '1',
                 overview = None, director = None, guest_stars = [],
                 rating = None, writer = None, watched = False,
                 air_date = '', id = -1):
        self.id = id
        self.name = name
        self.show = show
        self.episode_number = episode_number
        self.season_number = season_number
        self.overview = overview
        self.director = director
        self.guest_stars = guest_stars
        self.rating = rating
        self.writer = writer
        self.watched = watched
        try:
            self.air_date = datetime.strptime(air_date, '%Y-%m-%d').date()
        except:
            self.air_date = ''

    def get_episode_show_number(self):
        return '%sx%02d' % (self.season_number, int(self.episode_number))

    def __repr__(self):
        return self.get_title()

    def get_dict(self):
        return {'episodeName': self.get_title(),
                'episodeNumber': self._get_episode_number(),
                'airDate': self.get_air_date_text(),
                'airDateFormat': self.air_date,
                'isWatched': self.get_watched(),
                'hasAired': self.already_aired(),
                'episodeRating': self.get_rating(),
                'overviewText': self.get_overview()}

    def get_rating(self):
        try:
            return round(float(self.rating))
        except:
            return 0

    def get_title(self):
        return _('Ep. %s: %s') % (self.get_episode_show_number(), self.name)

    def get_watched(self): return self.watched
    def set_watched(self, watched):
        if (self.watched == watched):
            return
        self.watched = watched
        pyotherside.send('watchedChanged', self.watched)
        pyotherside.send('infoMarkupChanged')
        SeriesManager().updated()

    def get_overview(self):
        return self.overview
    def set_overview(self, overview):
        self.overview = overview
        pyotherside.send('overviewChanged', self.overview)

    def __eq__(self, episode):
        if not episode:
            return False
        return self.show == episode.show and \
               self.episode_number == episode.episode_number and \
               self.season_number == episode.season_number

    def merge_episode(self, episode):
        self.name = episode.name or self.name
        self.show = episode.show or self.show
        self.episode_number = episode.episode_number or self.episode_number
        self.season_number = episode.season_number or self.season_number
        self.overview = episode.overview or self.overview
        self.director = episode.director or self.director
        self.guest_stars = episode.guest_stars or self.guest_stars
        self.rating = episode.rating or self.rating
        self.writer = episode.writer or self.writer
        self.watched = episode.watched or self.watched
        self.air_date = episode.air_date or self.air_date
        #TODO: Maybe we should check which really changed and only emit those signals?
        #self.ratingChanged.emit()
        #self.titleChanged.emit()
        #self.airDateTextChanged.emit()
        #self.watchedChanged.emit()

    def get_air_date_text(self):
        if not self.air_date:
            return ''
        today = datetime.today().date()
        if today == self.air_date:
            return _('Today')
        if today + timedelta(days = 1) == self.air_date:
            return _('Tomorrow')
        if today - timedelta(days = 1) == self.air_date:
            return _('Yesterday')
        next_air_date_str = self.air_date.strftime('%d %b')
        if self.air_date.year != datetime.today().year:
            next_air_date_str += self.air_date.strftime(' %Y')
        return next_air_date_str

    def already_aired(self):
        if self.air_date and self.air_date <= datetime.today().date():
            return True
        return False

    def _get_episode_number(self):
        return self._episode_number

    def _set_episode_number(self, number):
        try:
            self._episode_number = int(number)
        except ValueError:
            self._episode_number = 1
        pyotherside.send('titleChanged')

    def _get_air_date(self):
        return self._air_date

    def _set_air_date(self, new_air_date):
        if type(new_air_date) != str:
            self._air_date = new_air_date
            return
        for format in ['%Y-%m-%d', '%d-%m-%Y', '%Y/%m/%d', '%d/%m/%Y',
                       '%m-%d-%Y', '%d %b %Y']:
            try:
                self._air_date = datetime.strptime(new_air_date, format).date()
            except ValueError:
                continue
            else:
                return
        self._air_date = None
        pyotherside.send('airDateTextChanged', self._air_date)

    def get_info_markup(self):
        color = get_color(constants.SECONDARY_TEXT_COLOR)
        if not self.watched and self.already_aired():
            color = get_color(constants.ACTIVE_TEXT_COLOR)
        return '<span foreground="%s">%s\n%s</span>' % \
               (color, saxutils.escape(str(self)), self.get_air_date_text())

    def updated(self):
        SeriesManager().updated()

    episode_number = property(_get_episode_number, _set_episode_number)
    air_date = property(_get_air_date, _set_air_date)

    def get_most_recent(self, other_episode):
        if not other_episode:
            if self.already_aired():
                return self
            else:
                return None
        if other_episode.already_aired() and not self.already_aired():
            return other_episode
        if self.already_aired() and not other_episode.already_aired():
            return self
        if not self.already_aired() and not other_episode.already_aired():
            return None
        if self.air_date > other_episode.air_date:
            return self
        if self.air_date < other_episode.air_date:
            return other_episode
        return None

class SeriesManager(object):

    GET_FULL_SHOW_COMPLETE_SIGNAL = 'get-full-show-complete'
    # UPDATE_SHOW_EPISODES_COMPLETE_SIGNAL = 'update-show-episodes-complete'
    # UPDATE_SHOWS_CALL_COMPLETE_SIGNAL = 'update-shows-call-complete'
    # SHOW_LIST_CHANGED_SIGNAL = 'show-list-changed'

    _instance = None
    _instance_initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance:
            return cls._instance

        cls._instance = super(SeriesManager, cls).__new__(
            cls, *args, **kwargs)
        return cls._instance

    def __init__(self):
        if not SeriesManager._instance_initialized:
            SeriesManager._instance_initialized = True

            self.series_list = []

            self.thetvdb = thetvdbapi.TheTVDB(TVDB_API_KEY)
            self.changed = False
            self.auto_save_id = None

            # Cached values
            self._cached_tvdb_shows = {}

            # Searching
            self.searching = False
            self.search_results = []

            # Languages
            # self.languages = self.thetvdb.get_available_languages()
            self.languages = self._load_languages(SF_LANG_FILE)
            self.default_language = None

            self.have_deleted = False
            self.isUpdating = False
            self.isLoading = False

    def get_loading(self):
        return self.isLoading

    def get_updating(self):
        return self.isUpdating

    def update_languages(self):
        self.languages = self.thetvdb.get_available_languages()
        self._save_languages(SF_LANG_FILE)
        return self.languages

    def get_languages(self):
        if self.languages is None:
            return self.update_languages()
        return self.languages

    def get_default_language(self):
        if self.default_language is None:
            #Get local supported languages
            local_languages = []
            lc, encoding = locale.getdefaultlocale()
            if lc:
                local_languages = [lc]
            local_languages += ['es_ES']
            local_languages += constants.DEFAULT_LANGUAGES
            #Find language
            self.default_language = 'en'
            for lang in local_languages:
                code = lang.split('_')[0]
                if self.get_languages().has_key(code):
                    self.default_language = code
                    break

        return self.default_language

    def _load_languages(self, file_path):
        if not os.path.exists(file_path):
            return None
        return serializer.deserialize(file_path)

    def _save_languages(self, file_path):
        dirname = os.path.dirname(file_path)
        if not (os.path.exists(dirname) and os.path.isdir(dirname)):
            os.mkdir(dirname)
        serialized = serializer.serialize(self.languages)
        save_file = open(file_path, 'w')
        save_file.write(serialized)
        save_file.close()

    def get_searching(self): return self.searching

    def search_shows(self, terms, language = "en"):
        if not terms:
            return []
        self.search_results = []
        self.searching = True
        pyotherside.send('searching', self.searching)
        async_worker = AsyncWorker(True)
        async_item = AsyncItem(self.thetvdb.get_matching_shows,
                               (terms, language,),
                               self._search_finished_callback)
        async_worker.queue.put(async_item)
        async_worker.start()

    def search_result_model(self):
        search_results_list = []
        for item in self.search_results:
            search_results_list.append({'data': item})
        return search_results_list

    def _search_finished_callback(self, tvdbshows, error):
        if not error:
            for show_id, show in tvdbshows:
                self._cached_tvdb_shows[show_id] = show
                self.search_results.append(show)
        self.searching = False
        pyotherside.send('searching', self.searching)

    def update_show_episodes(self, show):
        return self.update_all_shows_episodes([show])

    def update_show_by_name(self, show_name):
        show = self.get_show_by_name(show_name)
        return self.update_all_shows_episodes([show])

    def update_all_shows_episodes(self, show_list = []):
        self.isUpdating = True
        pyotherside.send('updating', self.isUpdating)
        show_list = show_list or self.series_list
        async_worker = AsyncWorker(False)
        update_images_worker = AsyncWorker(True)
        i = 0
        n_shows = len(show_list)
        for i in range(n_shows):
            update_ended_shows = Settings().getConf(Settings.UPDATE_ENDED_SHOWS)
            show = show_list[i]
            if show.status and show.status == 'Ended' and not update_ended_shows:
                async_item = AsyncItem(self._empty_callback, (),
                                       self._set_show_episodes_complete_cb,
                                       (show, update_images_worker, i == n_shows - 1))
                async_worker.queue.put(async_item)
                continue
            pyotherside.send('episodesListUpdating', show.get_name())
            show.set_updating(True)
            async_item = AsyncItem(self.thetvdb.get_show_and_episodes,
                                   (show.thetvdb_id, show.language,),
                                   self._set_show_episodes_complete_cb,
                                   (show, update_images_worker, i == n_shows - 1))
            async_worker.queue.put(async_item)
            async_item = AsyncItem(self._set_show_images,
                                   (show,),
                                   None,)
            update_images_worker.queue.put(async_item)
        async_worker.start()
        return async_worker

    def _set_show_episodes_complete_cb(self, show, images_worker, last_call, tvdbcompleteshow, error):
        if not error and tvdbcompleteshow:
            show = self._update_show_from_thetvdbshow(show, tvdbcompleteshow[0])
            episode_list = list([self._convert_thetvdbepisode_to_episode(tvdb_ep,show) \
                            for tvdb_ep in tvdbcompleteshow[1]])
            show.update_episode_list(episode_list)
        #pyotherside.send('updateShowEpisodesComplete', show)
        show.set_updating(False)
        pyotherside.send('episodesListUpdated', show.get_dict())
        if last_call:
            #pyotherside.send('updateShowsCallComplete', show)
            self.isUpdating = False
            pyotherside.send('updating', self.isUpdating)
            images_worker.start()

    def _search_show_to_update_callback(self, tvdbshows):
        if not tvdbshows:
            # FIXME: raise error
            return
        for show_id, show in tvdbshows:
            pass

    def _empty_callback(self):
        pass

    def get_complete_show(self, show_name, language = "en"):
        # Test if the show has already been added.
        for show in self.series_list:
            if show.name == show_name:
                pyotherside.send('notification', 'Show already in library')
                return
        self.isUpdating = True
        pyotherside.send('updating', self.isUpdating)
        show_id = self._cached_tvdb_shows.get(show_name, None)
        for show_id, show_title in self._cached_tvdb_shows.items():
            if show_title == show_name:
                break
        if not show_id:
            return
        async_worker = AsyncWorker(False)
        async_item = AsyncItem(self._get_complete_show_from_id,
                               (show_id, language,),
                               self._get_complete_show_finished_cb)
        async_worker.queue.put(async_item)
        async_worker.start()

    def _get_complete_show_from_id(self, show_id, language):
        tvdb_show_episodes = self.thetvdb.get_show_and_episodes(show_id, language)
        if not tvdb_show_episodes:
            return None
        show = self._convert_thetvdbshow_to_show(tvdb_show_episodes[0])
        show.id = self._get_id_for_show()
        show.episode_list = []
        for tvdb_ep in tvdb_show_episodes[1]:
            episode = self._convert_thetvdbepisode_to_episode(tvdb_ep,
                                                              show)
            add_special_seasons = Settings().getConf(Settings.ADD_SPECIAL_SEASONS)
            if not add_special_seasons and \
               show.is_special_season(episode.season_number):
                continue
            show.episode_list.append(episode)
        self.series_list.append(show)
        self.changed = True
        self.isUpdating = False
        pyotherside.send('showListChanged', self.changed)
        pyotherside.send('updating', self.isUpdating)
        return show

    def _get_complete_show_finished_cb(self, show, error):
        logging.debug('GET_FULL_SHOW_COMPLETE_SIGNAL') #TODO
        async_worker = AsyncWorker(True)
        async_item = AsyncItem(self._set_show_images,
                               (show,),
                               None,)
        async_worker.queue.put(async_item)
        async_worker.start()

    def get_show_by_id(self, show_id):
        for show in self.series_list:
            if show.id == show_id:
                return show
        return None

    def get_show_by_name(self, show_name):
        for show in self.series_list:
            if show.name == show_name:
                return show
        return None

    def get_series_list(self):
        series_list = []
        for show in self.series_list:
            series_list.append(show.get_dict())
        return SortedSeriesList(series_list, Settings())

    def get_seasons_list(self, show_name):
        show = self.get_show_by_name(show_name)
        return show.get_seasons_model()

    def get_episodes_list(self, show_name, season):
        show = self.get_show_by_name(show_name)
        return show.get_sorted_episode_list_by_season(season)

    def get_upcoming_episodes_list(self, show_name, season=None):
        episode_list = []
        show = self.get_show_by_name(show_name)
        episodes = show.get_episodes_by_season(season)
        for episode in episodes:
            if not episode.already_aired():
                if not episode.watched:
                    episode_list.append(episode.get_dict())
        return episode_list

    def set_episode_watched(self, watched, show_name, episode_name):
        show = self.get_show_by_name(show_name)
        episode = show._get_episode_by_name(episode_name)
        episode.set_watched(watched)

    def mark_all_episodes_watched(self, watched, show_name, season=None):
        show = self.get_show_by_name(show_name)
        show._mark_all_episodes(watched, season)

    def mark_next_episode_watched(self, watched, show_name, season=None):
        show = self.get_show_by_name(show_name)
        episodes_info = show.get_episodes_info()
        next_episode = episodes_info['next_episode']
        if next_episode:
            next_episode.set_watched(watched)

    def _convert_thetvdbshow_to_show(self, thetvdb_show):
        show_obj = Show(thetvdb_show.name, season_images = {})
        show_obj.language = thetvdb_show.language
        show_obj.status = thetvdb_show.status
        show_obj.runtime = thetvdb_show.runtime
        show_obj.airs_time = thetvdb_show.airs_time
        show_obj.genre = thetvdb_show.genre
        show_obj.set_overview(thetvdb_show.overview)
        show_obj.network = thetvdb_show.network
        show_obj.rating = thetvdb_show.rating
        show_obj.actors = thetvdb_show.actors
        show_obj.thetvdb_id = thetvdb_show.id
        show_obj.imdb_id = thetvdb_show.imdb_id
        return show_obj

    def _convert_thetvdbepisode_to_episode(self, thetvdb_episode, show = None):
        episode_obj = Episode(thetvdb_episode.name, show, thetvdb_episode.episode_number)
        episode_obj.show = show
        episode_obj.season_number = thetvdb_episode.season_number
        episode_obj.overview = thetvdb_episode.overview
        episode_obj.director = thetvdb_episode.director
        episode_obj.guest_stars = thetvdb_episode.guest_stars
        episode_obj.rating = thetvdb_episode.rating
        episode_obj.writer = thetvdb_episode.writer
        episode_obj.air_date = thetvdb_episode.first_aired or ''
        return episode_obj

    def _update_show_from_thetvdbshow(self, show_obj, thetvdb_show):
        show_obj.language = thetvdb_show.language
        show_obj.status = thetvdb_show.status
        show_obj.runtime = thetvdb_show.runtime
        show_obj.airs_time = thetvdb_show.airs_time
        show_obj.genre = thetvdb_show.genre
        show_obj.set_overview(thetvdb_show.overview)
        show_obj.network = thetvdb_show.network
        show_obj.rating = thetvdb_show.rating
        show_obj.actors = thetvdb_show.actors
        show_obj.thetvdb_id = thetvdb_show.id
        show_obj.imdb_id = thetvdb_show.imdb_id
        return show_obj

    def add_show(self, show):
        if show.id == -1:
            show.id = self._get_id_for_show()
        self.series_list.append(show)
        self.changed = True
        pyotherside.send('showListChanged', self.changed)

    def delete_show(self, show):
        for i in range(len(self.series_list)):
            if self.series_list[i] == show:
                if not self._get_shows_from_id:
                    for image in [show.image] + show.season_images.values():
                        if os.path.isfile(image):
                            os.remove(image)
                del self.series_list[i]
                self.changed = True
                self.have_deleted = True
                pyotherside.send('showListChanged', self.changed)
                break

    def delete_show_by_name(self, show_name):
        show = self.get_show_by_name(show_name)
        self.delete_show(show)

    def delete_season(self, show_name, season):
        show = self.get_show_by_name(show_name)
        show.delete_season(season)

    def _get_shows_from_id(self, id):
        return [show for show in self.series_list if show.id == id]

    def _get_id_for_show (self):
        id = 1
        for show in self.series_list:
            id = show.id
            if show.id >= id:
                id += 1
        return id

    def _set_show_images(self, show):
        thetvdb_id = show.thetvdb_id
        if thetvdb_id == -1:
            return
        self._assign_existing_images_to_show(show)
        seasons = show.get_seasons()
        for key, image in list(show.season_images.items()):
            if not os.path.isfile(image):
                del show.season_images[key]
                self.changed = True
        # Check if the download is needed
        if len(seasons) == len(show.season_images.keys()) and \
           show.image and os.path.isfile(show.image):
            pyotherside.send('showArtChanged')
            return
        image_choices = self.thetvdb.get_show_image_choices(thetvdb_id)
        for image in image_choices:
            image_type = image[1]
            url = image[0]
            if image_type  == 'poster' and \
               (not show.image or not os.path.isfile(show.image)):
                show.downloading_show_image = True
                pyotherside.send('showArtChanged')
                target_file = os.path.join(DATA_DIR, show.get_poster_prefix())
                image_file = os.path.abspath(image_downloader(url, target_file))
                show.set_cover_image(image_file)
                show.downloading_show_image = False
                self.changed = True
                pyotherside.send('showArtChanged')
            elif image_type == 'season':
                season = image[3]
                if season in seasons and \
                   season not in show.season_images.keys():
                    show.downloading_season_image = True
                    pyotherside.send('showArtChanged')
                    target_file = os.path.join(DATA_DIR,
                                               show.get_season_poster_prefix(season))
                    try:
                        image_file = os.path.abspath(image_downloader(url,
                                                                      target_file))
                    except Exception as exception:
                        logging.debug(str(exception))
                    else:
                        show.season_images[season] = image_file
                    show.downloading_season_image = False
                    self.changed = True
                    pyotherside.send('showArtChanged')
            if show.image and len(show.season_images) == len(seasons):
                break

    def _assign_existing_images_to_show(self, show):
        for archive in os.listdir(DATA_DIR):
            if archive.startswith(show.get_season_poster_prefix()):
                show.assign_image_to_season(os.path.join(DATA_DIR, archive))
                self.changed = True
            elif archive.startswith(show.get_poster_prefix()):
                show.set_cover_image(os.path.abspath(os.path.join(DATA_DIR, archive)))
                self.changed = True

    def save(self, save_file_path):
        if not self.changed:
            return True

        dirname = os.path.dirname(save_file_path)
        if not (os.path.exists(dirname) and os.path.isdir(dirname)):
            os.mkdir(dirname)
        serialized = serializer.serialize(self.series_list)
        save_file_path_tmp = save_file_path + ".tmp"
        save_file = open(save_file_path_tmp, 'w')
        save_file.write(serialized)
        save_file.close()
        os.rename(save_file_path_tmp, save_file_path)
        self.changed = False
        return True

    def timerEvent(self, event):
        if (event.timerId() == self.auto_save_id):
            self.save(constants.SF_DB_FILE)

    def load(self, file_path):
        if not os.path.exists(file_path):
            return
        self.isLoading = True
        pyotherside.send('loadingChanged', self.isLoading)
        for serie in serializer.deserialize(file_path):
            self.series_list.append(serie)
        self.changed = False
        pyotherside.send('showListChanged', self.changed)
        self.isLoading = False
        pyotherside.send('loadingChanged', self.isLoading)

    def updated(self):
        self.changed = True
        #pyotherside.send('showListChanged', self.changed)

    def auto_save(self, activate = True):
        if activate and not self.auto_save_id:
            self.auto_save_id = self.startTimer(constants.SAVE_TIMEOUT_MS)
        elif self.auto_save_id and not activate:
            self.killTimer(self.auto_save_id)
            self.auto_save_id = None
