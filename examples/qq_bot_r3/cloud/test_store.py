import unittest,tempfile,pathlib,sys,json,time
sys.path.insert(0,str(pathlib.Path(__file__).parent))
from naiwa_store import Store,atomic,digest,allowed,key_for

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name);self.store=Store(self.root)
        self.policy={'version':1,'ownerHash':digest('owner:owner'),'groupHashes':[digest('group:group')]}
    def tearDown(self):self.temp.cleanup()
    def test_permissions(self):
        self.assertTrue(allowed(self.policy,None,'owner'));self.assertFalse(allowed(self.policy,None,'stranger'));self.assertTrue(allowed(self.policy,'group','member'));self.assertFalse(allowed(self.policy,'other','owner'))
    def test_invalid_policy(self):
        for p in [None,{},dict(self.policy,version=2),dict(self.policy,groupHashes=['bad'])]:self.assertFalse(allowed(p,None,'owner'))
        self.assertFalse(allowed(self.policy,'group',''))
    def test_member_isolation(self):
        a=key_for('group','a');b=key_for('group','b');self.store.commit(a,'secret-a','answer-a');self.assertEqual(self.store.chat(b)['msgs'],[])
    def test_group_private_isolation(self):self.assertNotEqual(key_for(None,'a'),key_for('group','a'))
    def test_auto_commit(self):
        self.store.commit('a','q','a');self.assertEqual(len(self.store.chat('a')['msgs']),2)
    def test_manual_no_history(self):
        self.store.commit('a','q','a');atomic(self.root/'memory-settings.json',{'mode':'manual'});self.store.commit('a','ignored','ignored');self.assertEqual(len(self.store.chat('a')['msgs']),2);self.assertEqual(self.store.prepare('a','q')[0],[])
    def test_off_no_save_no_notes_context(self):
        self.store.command('/记住 personal-note','a');atomic(self.root/'memory-settings.json',{'mode':'off'});self.store.commit('a','q','a');self.assertEqual(self.store.chat('a')['msgs'],[]);self.assertNotIn('personal-note',self.store.prepare('a','personal-note')[1])
    def test_new_conversation_retains_archive(self):
        self.store.commit('a','q','a');self.store.command('/新对话','a');self.assertEqual(len(self.store.chat('a')['msgs']),2);self.assertEqual(self.store.prepare('a','q')[0],[])
    def test_notes_remove(self):
        self.store.command('/记住 first','a');self.store.command('/记住 second','a');self.store.command('/忘记 1','a');self.assertEqual(self.store.notes('a'),['second'])
    def test_corrupt_memory_fails(self):
        atomic(self.root/'memory-settings.json',{'mode':'invalid'});self.assertRaises(ValueError,self.store.mode)
    def test_legacy_context_and_background(self):
        self.store.commit('a','who likes oranges','oranges');self.store.command('/记住 oranges','a');recent,background,mode=self.store.prepare('a','oranges');self.assertEqual(mode,'auto');self.assertEqual(len(recent),2);self.assertIn('oranges',background)
    def test_history_validation(self):
        atomic(self.store.chat_path('a'),{'msgs':[{'role':'assistant','content':'bad'}]});self.assertRaises(ValueError,self.store.chat,'a')
    def test_commit_stores_split_times_and_contexts_stay_clean(self):
        self.store.commit('a','q1','a1');self.store.commit('a','q2','a2',asked_at=int(time.time()*1000)-120000)
        msgs=self.store.chat('a')['msgs']
        self.assertTrue(all(isinstance(m['ts'],int) and not isinstance(m['ts'],bool) for m in msgs))
        self.assertTrue(all(set(m)=={'role','content','ts'} for m in msgs))
        self.assertLess(msgs[2]['ts'],msgs[3]['ts'])
        recent,background,_=self.store.prepare('a','q2')
        self.assertTrue(all(set(m)=={'role','content'} for m in recent))
        node=json.loads(background)
        self.assertEqual(node['historyLength'],4)
        self.assertEqual([x['historyIndex'] for x in node['recentTiming']],[0,1,2,3])
        self.assertNotIn('untimedHistoryIndexes',node)
    def test_asked_at_falls_back_when_unusable(self):
        now=int(time.time()*1000)
        for bad in (None,'1700000000000',True,0,-1,now+10**8):
            self.store.commit('a','q','a',asked_at=bad)
        self.assertTrue(all(isinstance(m['ts'],int) and m['ts']>=now for m in self.store.chat('a')['msgs']))
    def test_speaker_is_low_trust_background_only(self):
        recent,background,_=self.store.prepare('a','q',speaker='  群主   本人  ',tag='0123456789ab')
        node=json.loads(background)
        self.assertEqual(node['currentSpeaker'],{'displayName':'群主 本人','memberRef':'0123456789ab'})
        self.assertNotIn('currentSpeaker',json.dumps(recent))
        _,background,_=self.store.prepare('a','q',speaker='x',tag='not-a-tag')
        self.assertEqual(json.loads(background)['currentSpeaker'],{'displayName':'x','memberRef':''})
        _,background,_=self.store.prepare('a','q')
        self.assertNotIn('currentSpeaker',background)
    def test_legacy_archive_without_timestamps_still_prepares(self):
        atomic(self.store.chat_path('a'),{'t':0,'msgs':[{'role':'user','content':'old-q'},{'role':'assistant','content':'old-a'}]})
        recent,background,mode=self.store.prepare('a','anything')
        self.assertEqual(len(recent),2)
        node=json.loads(background)
        self.assertEqual(node['untimedHistoryIndexes'],[0,1]);self.assertNotIn('recentTiming',node)
    def test_invalid_timestamp_type_fails_closed(self):
        for bad in (True,'1700000000000',None):
            atomic(self.store.chat_path('a'),{'t':0,'msgs':[{'role':'user','content':'q','ts':bad},{'role':'assistant','content':'a'}]})
            self.assertRaises(ValueError,self.store.chat,'a')

    def test_group_listen_defaults_off(self):
        self.assertEqual(self.store.listen_mode(),'off')
        self.assertFalse(self.store.record_group('group',{'t':1,'member':'a'*12,'name':'n','text':' chatter '}))
        self.assertFalse((self.root/'group-stream').exists())
        self.assertEqual(self.store.group_stream('group'),[])
    def test_group_listen_records_and_reads_back(self):
        atomic(self.root/'group-listen.json',{'mode':'on'})
        now=int(time.time()*1000)
        self.assertTrue(self.store.record_group('group',{'t':now,'member':'a'*12,'name':'  张三  ','text':'x'*400}))
        self.assertFalse(self.store.record_group('group',{'t':now,'member':'a'*12,'name':'张三','text':'   '}))
        self.assertTrue(self.store.record_group('other',{'t':now,'member':'c'*12,'name':'王五','text':'in-other'}))
        self.assertEqual([json.loads(raw)['member'] for raw in self.store.group_log_path('group').read_text(encoding='utf-8').splitlines()],['a'*12])
        self.assertEqual([json.loads(raw)['member'] for raw in self.store.group_log_path('other').read_text(encoding='utf-8').splitlines()],['c'*12])
        self.assertEqual(self.store.group_stream('group'),['张三: '+'x'*300])
        self.assertEqual(self.store.group_stream('other'),['王五: in-other'])
        self.assertEqual(self.store.group_stream('unknown'),[])
        self.assertEqual(self.store.group_stream('group',minutes=5,budget=10),[])
    def test_group_listen_stale_entries_expire(self):
        atomic(self.root/'group-listen.json',{'mode':'on'})
        old=int(time.time()*1000)-40*60000
        self.store.record_group('group',{'t':old,'member':'a'*12,'name':'张三','text':'earlier'})
        self.assertEqual(self.store.group_stream('group',minutes=30),[])
        self.assertEqual(len(self.store.group_stream('group',minutes=60)),1)
    def test_group_stream_enters_background_only_when_listening(self):
        atomic(self.root/'group-listen.json',{'mode':'on'})
        self.store.record_group('group',{'t':int(time.time()*1000),'member':'a'*12,'name':'张三','text':'明天记得带钥匙'})
        _,background,mode=self.store.prepare(key_for('group','someone'),'钥匙','group',speaker='李四',tag='b'*12)
        data=json.loads(background)
        self.assertEqual(data['groupStream'],['张三: 明天记得带钥匙'])
        self.assertIn('groupStream',data['scope'])
        self.assertEqual(data['currentSpeaker']['displayName'],'李四')
        atomic(self.root/'group-listen.json',{'mode':'off'})
        self.assertNotIn('groupStream',json.loads(self.store.prepare(key_for('group','someone'),'钥匙','group')[1]))

    def test_status_reports_configured_model_not_a_hardcoded_one(self):
        self.assertEqual(self.store.model_name(),'model')
        atomic(self.root.parent/'cmd_config.json',{'provider':[{'model':'  deepseek-v4.1-flash  '}]})
        self.assertEqual(self.store.model_name(),'deepseek-v4.1-flash')
        status=self.store.command('/状态',key_for('group','someone'))
        self.assertIn('deepseek-v4.1-flash',status)
        self.assertNotIn('GPT 6 Luna',status)
        atomic(self.root.parent/'cmd_config.json',{'provider':[{'model':''}]})
        self.assertEqual(self.store.model_name(),'model')

    def test_game_data_requires_a_group_and_a_database(self):
        self.assertEqual(self.store.game_data('毁灭胸甲的数值是多少'),[])
        self.assertEqual(self.store.game_data('毁灭胸甲的数值是多少',),[])
        _,background,_=self.store.prepare(key_for('group','someone'),'毁灭胸甲的数值是多少','group')
        self.assertNotIn('gameData',json.loads(background))
        _,private,_=self.store.prepare(key_for(None,'someone'),'毁灭胸甲的数值是多少',None)
        self.assertNotIn('gameData',json.loads(private))

if __name__=='__main__':unittest.main()
