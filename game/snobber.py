import logging
import re
import json
from core.extractors import Extractor


class SnobManager:
    wrapper = None
    village_id = None
    resman = None
    can_snob = True
    troop_manager = None
    wanted = 1
    building_level = 0
    is_incomplete = False

    def level_system(self):
        return 0

    def __init__(self, wrapper=None, village_id=None):
        self.wrapper = wrapper
        self.village_id = village_id
        self.logger = logging.getLogger("Snob:%s" % self.village_id)

    def need_reserve(self, text):
        need_amount = re.search(r'(?s)<th colspan="3">[\w\s]+</th>.+?data-unit="snob">.+?<td.+?>\s*(\d+)\sx', text)
        if need_amount:
            return int(need_amount.group(1))
        return 0

    def attempt_recruit(self, amount):
        result = self.wrapper.get_action(action="snob", village_id=self.village_id)
        game_data = Extractor.game_state(result)
        self.resman.update(game_data)
        nres = self.need_reserve(result.text)
        if nres > 0:
            self.logger.debug("Not enough resources available, still %d needed, attempting storage" % nres)
            cres = self.coin(result.text)
            if cres:
                return self.attempt_recruit(amount)
            else:
                self.is_incomplete = True
                self.logger.debug("Not enough resources available")
                return False
        self.is_incomplete = False
        can_recruit = re.search(r'(?s)<th>Er kan nog geproduceerd worden:</th>\s*<th>(\d+)<', result.text)
        if not can_recruit:
            self.logger.warning('Error fetching current snob number')
            return False
        r_num = int(can_recruit.group(1))
        if r_num == 0:
            self.logger.debug("No more snobs available, awaiting snob creating, snob death or village loss")
            return False

        return False

    def coin(self, result):
        coin_re = re.search(r'train\.storage_item = (\{.+?\})', result)
        if not coin_re:
            self.logger.warning("Snob recruit is called but storage data not on page, error?")
            return False
        raw_coin = coin_re.group(1)
        data = json.loads(raw_coin)

        if self.has_enough(data):
            get_post = "game.php?village=%s&screen=snob&action=reserve" % self.village_id
            data = {
                'factor': '1',
                'h': self.wrapper.last_h
            }
            self.wrapper.post_url(url=get_post, data=data)
            return True
        else:
            self.is_incomplete = True
            return False

    def has_enough(self, build_item):
        r = True
        if build_item['wood'] > self.resman.actual['wood']:
            req = build_item['wood'] - self.resman.actual['wood']
            self.resman.request(source="snob", resource="wood", amount=req)
            r = False
        if build_item['stone'] > self.resman.actual['stone']:
            req = build_item['stone'] - self.resman.actual['stone']
            self.resman.request(source="snob", resource="stone", amount=req)
            r = False
        if build_item['iron'] > self.resman.actual['iron']:
            req = build_item['iron'] - self.resman.actual['iron']
            self.resman.request(source="snob", resource="iron", amount=req)
            r = False
        return r

    def run(self):
        if not self.can_snob:
            return False
        if self.building_level == 0:
            return False
        if self.wanted > 0:
            if 'snob' not in self.troop_manager.total_troops:
                return self.attempt_recruit(amount=self.wanted)

            current = int(self.troop_manager.total_troops['snob'])
            if current < self.wanted:
                return self.attempt_recruit(amount=self.wanted - current)
            self.logger.info("Snob up-to-date (%d/%d)" % (current, self.wanted))
