# 人物立绘生图提示词

图片存放路径：`web/public/portraits/`

- **大臣/武将**：`minister_<中文名>.png`（专属头像，文件名 = content 里人物姓名，如 `minister_王承恩.png`）。
  前端按 `minister_<姓名>.png` 直接找专属图，无则回退占位符。
- **后宫**：不做逐角色专属图，改用**预设图池** `consort_pool_<N>.png`（N=1..20）。
  选妃/册封时大模型生成新妃嫔，代码 `db.next_pool_portrait_id("consort_pool_")` 顺序分配池编号，
  前端按 `portrait_id` 取 `consort_pool_<N>.png`。content 里开局 5 位后宫已预绑 pool 1–5。
  详见文末「后宫预设图池」。

---

## 风格基底

所有大臣立绘末尾统一加：
> Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail

所有后宫立绘末尾统一加（风格各人不同，见各人说明）：
> white background, vertical composition 3:4, high detail, masterwork quality

---

## 大臣立绘（41位史实 + 19位虚构，共60位）

### 皇党

#### 王承恩 `minister_wang_chengen.png`
```
Wang Chengen, Ming dynasty loyal eunuch official, round kind face, slightly plump, deep purple palace eunuch robes, hands folded submissively, gentle warm eyes, loyal devoted expression. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 曹化淳 `minister_cao_huachun.png`
```
Cao Huachun, Ming dynasty eunuch official candidate for chief eunuch, middle-aged, broad face, calm measured expression, dark grey-purple eunuch court robe, hands clasped at waist, watchful intelligent eyes. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 温体仁 `minister_wen_tiren.png`
```
Wen Tiren, Ming dynasty Minister of Rites opportunist, middle-aged, smooth ingratiating smile, red senior official robe, well-groomed thin beard, slightly obsequious posture, calculating eyes. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 周延儒 `minister_zhou_yanru.png`
```
Zhou Yanru, Ming dynasty young rising official, handsome middle-aged face, elegant bearing, medium blue official robe, confident slight smile, polished scholarly appearance, ambitious bright eyes. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 毕自严 `minister_bi_ziyan.png`
```
Bi Ziyan, Ming dynasty Minister of Revenue, lean middle-aged scholar, wire-rimmed official cap, worried furrowed brow, blue-grey robe, holding a ledger scroll, careful meticulous expression. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

---

### 阉党

#### 魏忠贤 `minister_wei_zhongxian.png`
```
Wei Zhongxian, Ming dynasty powerful chief eunuch, portly imposing figure, opulent embroidered eunuch robe with dragon motifs, domineering arrogant expression, heavy jowls, small cruel eyes, one hand on hip. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes, high detail
```

#### 客氏 `minister_ke_shi.png`
```
Ke Shi, Ming dynasty imperial wet nurse noblewoman, middle-aged woman, elaborate headdress, rich dark-embroidered noblewoman robe, proud haughty expression, hands holding a fan, heavy makeup. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, high detail
```

#### 崔呈秀 `minister_cui_chengxiu.png`
```
Cui Chengxiu, Ming dynasty Minister of War under eunuch faction, stocky middle-aged man, aggressive confident stance, dark red official robe, short beard, intimidating expression, one hand on sword pommel. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 田尔耕 `minister_tian_ergeng.png`
```
Tian Ergeng, Ming dynasty Jinyiwei commander, military bearing, black armor-trimmed official robe, Jinyiwei flying fish emblem, sharp hawk-like eyes, hand resting on decorated sword hilt, cold expression. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes, high detail
```

#### 许显纯 `minister_xu_xianchun.png`
```
Xu Xianchun, Ming dynasty Jinyiwei interrogation officer, lean sinister face, dark black robe, sharp predatory eyes, thin cruel smile, holding a folded document, unsettling stillness. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes, high detail
```

#### 王体乾 `minister_wang_tiqian.png`
```
Wang Tiqian, Ming dynasty chief eunuch Directorate of Ceremonial, elderly eunuch, sunken aged face, heavy ceremonial robe with gold trim, hands hidden in sleeves, blank expressionless gaze, ceremonial staff. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes, high detail
```

#### 黄立极 `minister_huang_liji.png`
```
Huang Liji, Ming dynasty Grand Secretary serving eunuch faction, middle-aged, cautious hunched posture, dark maroon senior robe, thin mustache, downcast avoiding gaze, nervous servile manner. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 施凤来 `minister_shi_fenglai.png`
```
Shi Fenglai, Ming dynasty Vice Grand Secretary, pudgy soft-faced middle-aged official, pale blue senior robe, bland forgettable features, weak smile, hands meekly clasped, lacking presence. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 张瑞图 `minister_zhang_ruitu.png`
```
Zhang Ruitu, Ming dynasty Grand Secretary calligrapher-official, scholarly thin-faced middle-aged man, ink-stained fingers, elegant dark blue robe, holding a calligraphy brush, conflicted uneasy expression, artistic refined bearing. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 来宗道 `minister_lai_zongdao.png`
```
Lai Zongdao, Ming dynasty Minister of Rites eunuch-faction, elderly rotund official, ceremonial red robe with rank badge, round jovial face masking cunning, hands folded in formal greeting. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 王绍徽 `minister_wang_shaohui.png`
```
Wang Shaohui, Ming dynasty Minister of Personnel eunuch-faction, sharp angular face, dark official robe, piercing assessing eyes, slight sneer, posture projecting bureaucratic authority. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 阎鸣泰 `minister_yan_mingtai.png`
```
Yan Mingtai, Ming dynasty Ji-Liao Governor-General, heavyset northern official, practical travel-worn robe, thick beard, weathered face, map scroll tucked under arm, harried expression. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

---

### 东林

#### 韩爌 `minister_han_kuang.png`
```
Han Kuang, Ming dynasty former Grand Secretary Donglin elder, elderly distinguished man, long white flowing beard, deep blue senior robe, upright dignified posture despite age, wise steady eyes, gravitas of a retired statesman. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 李标 `minister_li_biao.png`
```
Li Biao, Ming dynasty Right Deputy Minister of Rites Donglin, upright middle-aged scholar, clean honest face, medium blue robe, hands clasped respectfully, frank direct gaze, moral integrity evident in bearing. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 黄道周 `minister_huang_daozhou.png`
```
Huang Daozhou, Ming dynasty Hanlin Academy compiler Donglin firebrand, sharp-featured intense young scholar, plain dark robe, passionate fierce eyes, holding a memorial document, righteous indignant expression. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 倪元璐 `minister_ni_yuanlu.png`
```
Ni Yuanlu, Ming dynasty Hanlin Academy compiler, refined literary young official, thin elegant face, scholar's robe, ink-brush tucked in belt, calm thoughtful expression, artist-statesman bearing. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 孙承宗 `minister_sun_chengzong.png`
```
Sun Chengzong, Ming dynasty former supreme commander elder statesman, white-haired elderly general-scholar, wearing official robe over partial campaign armor, white long beard, serene commanding presence, battle-experienced steady gaze. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 袁可立 `minister_yuan_keli.png`
```
Yuan Keli, Ming dynasty former Dengzhou governor Donglin, elderly retired official, simple dignified robe, kind weathered face, honest open expression, hands resting on walking staff. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 钱龙锡 `minister_qian_longxi.png`
```
Qian Longxi, Ming dynasty former Minister of Rites Donglin, refined elderly scholar, high jade-topped cap, formal ceremonial robe with rank badge, serene composed expression, cultured literary bearing. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 钱谦益 `minister_qian_qianyi.png`
```
Qian Qianyi, Ming dynasty celebrated literary official Donglin, portly distinguished elder, elaborate formal robe, full beard, self-satisfied scholarly expression, holding a poetry collection, renowned and vain bearing. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 刘鸿训 `minister_liu_hongxun.png`
```
Liu Hongxun, Ming dynasty diplomatic envoy returning from Korea, travel-worn middle-aged official, slightly dusty formal robe, road-weary but eager face, carrying diplomatic correspondence, alert pragmatic expression. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 卢象升 `minister_lu_xiansheng.png`
```
Lu Xiansheng, Ming dynasty Daming prefecture magistrate future general, young vigorous official, athletic build visible under robe, strong jaw, determined patriotic eyes, local administrative robe but military bearing. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 史可法 `minister_shi_kefa.png`
```
Shi Kefa, Ming dynasty young student scholar Donglin, youthful earnest face, plain student robes, bright idealistic eyes, holding study scrolls, upright morally resolute bearing despite youth. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

---

### 军队

#### 王之臣 `minister_wang_zhichen.png`
```
Wang Zhichen, Ming dynasty Ji-Liao supreme commander, senior military official, imposing build, campaign-worn armor under official robe, battle-scarred weathered face, commanding presence, hand on sword. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed armor and official hat, dignified expression, high detail
```

#### 满桂 `minister_man_gui.png`
```
Man Gui, Ming dynasty Datong garrison general, Mongolian-Han descent, broad powerful physique, full battle armor, fierce warrior face, thick black beard, battle-hardened fearless eyes. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed armor, dignified expression, high detail
```

#### 赵率教 `minister_zhao_sujiao.png`
```
Zhao Sujiao, Ming dynasty Shanhai Pass garrison general, lean veteran soldier, practical campaign armor, scarred resolute face, experienced tactical eyes, holding a battle map. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed armor, dignified expression, high detail
```

#### 祖大寿 `minister_zu_dashou.png`
```
Zu Dashou, Ming dynasty Jinzhou deputy garrison general, rugged northern warrior, heavy armor, broad shoulders, pragmatic calculating eyes, thick beard, imposing frontier military bearing. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed armor, dignified expression, high detail
```

#### 毛文龙 `minister_mao_wenlong.png`
```
Mao Wenlong, Ming dynasty Dongjiang garrison general island commander, flamboyant military figure, ornate decorated armor, confident arrogant posture, dramatic facial hair, self-styled heroic expression. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed armor, dignified expression, high detail
```

#### 袁崇焕 `minister_yuan_chonghuan.png`
```
Yuan Chonghuan, Ming dynasty former Liaodong governor brilliant general, tall broad-shouldered military man, partial armor over official robe, stern determined face, strong jaw, fearless strategic eyes, natural commander's bearing. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed armor and robes, dignified expression, high detail
```

#### 曹文诏 `minister_cao_wenzhao.png`
```
Cao Wenzhao, Ming dynasty Liaodong guerrilla general, fierce battle-hardened warrior, full campaign armor, aggressive combat-ready stance, scarred tough face, wild loyal eyes, sword at side. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed armor, dignified expression, high detail
```

---

### 中立

#### 王永光 `minister_wang_yongguang.png`
```
Wang Yongguang, Ming dynasty Minister of War neutral faction, solid dependable middle-aged official, standard dark official robe, measured composed expression, practical no-nonsense bearing, carefully neutral gaze. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 王在晋 `minister_wang_zaijin.png`
```
Wang Zaijin, Ming dynasty Nanjing Minister of War, stocky blunt-spoken middle-aged official, practical dark robe, gruff skeptical expression, arms crossed, no-nonsense pragmatist bearing. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 洪承畴 `minister_hong_chengchou.png`
```
Hong Chengchou, Ming dynasty Shaanxi grain transport official future general, ambitious middle-aged official, sharp calculating face, neat dark robe, cold intelligent eyes, composed ambition barely concealed, polished capable bearing. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 杨嗣昌 `minister_yang_sichang.png`
```
Yang Sichang, Ming dynasty Ministry of Revenue official, earnest young official, refined scholarly face, medium blue robe, diligent hardworking expression, ink-stained hands, administrator's careful bearing. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 孙传庭 `minister_sun_chuanting.png`
```
Sun Chuanting, Ming dynasty county magistrate future crusading general, young resolute county official, plain local magistrate robe, strong determined jaw, piercing eyes full of suppressed ambition, upright incorruptible bearing. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

---

### 西学

#### 徐光启 `minister_xu_guangqi.png`
```
Xu Guangqi, Ming dynasty former official scientist Catholic convert, elderly distinguished scholar, Western-influenced subtle details in robe trim, holding a telescope or astronomical instrument, keen observant eyes, open-minded intellectual expression. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

---

### 虚构人物（补足至60位）

#### 陈汝弼 `minister_chen_rubi.png` — 户部郎中·东林
```
Chen Rubi, fictional Ming dynasty Ministry of Revenue official Donglin, lean bookish young man, grey-blue robe, ink-stained fingers, spectacles, worried accountant's expression, carries abacus and ledger. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 冯应荣 `minister_feng_yingrong.png` — 工部侍郎·中立
```
Feng Yingrong, fictional Ming dynasty Vice Minister of Works, pragmatic engineer-official, stocky build, practical robe with work-worn edges, holding architectural plans, capable methodical expression, calloused hands. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 沈廷扬 `minister_shen_tingyang.png` — 刑部主事·东林
```
Shen Tingyang, fictional Ming dynasty Ministry of Justice official, upright young official, sharp righteous face, dark green judicial robe, holding a case file, unwavering honest eyes, incorruptible bearing. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 罗大经 `minister_luo_dajing.png` — 御史·东林
```
Luo Dajing, fictional Ming dynasty censorate inspector, fierce righteous young official, dark blue censor robe, accusatory pointing gesture, burning indignant eyes, clutching impeachment memorial. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 朱应槐 `minister_zhu_yinghuai.png` — 地方知府·中立
```
Zhu Yinghuai, fictional Ming dynasty prefectural magistrate, portly middle-aged local official, travel-worn robe, practical commonsense face, holds local population census scroll, experienced administrator bearing. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 高邦佐 `minister_gao_bangzuo.png` — 兵备道·军队
```
Gao Bangzuo, fictional Ming dynasty regional military intendant, weathered frontier official, mixed robe-and-armor attire, sun-darkened northern face, experienced tactical eyes, border defense map in hand. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and armor, dignified expression, high detail
```

#### 唐世济 `minister_tang_shiji.png` — 太仆寺卿·阉党
```
Tang Shiji, fictional Ming dynasty Court of Imperial Stud official eunuch-faction, fat comfortable official, expensive embroidered robe, self-satisfied greedy expression, counting jade beads, corrupt comfortable bearing. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, high detail
```

#### 万文英 `minister_wan_wenying.png` — 礼部主事·皇党
```
Wan Wenying, fictional Ming dynasty Ministry of Rites official loyalist, neat precise middle-aged official, formal ceremonial robe, meticulous appearance, solemn ritual-conscious expression, holding incense burner. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 郑三俊 `minister_zheng_sanjun.png` — 都察院御史·东林
```
Zheng Sanjun, fictional Ming dynasty Censorate inspector Donglin, thin ascetic elderly censor, plain dark robes, razor-sharp eyes missing nothing, tall upright posture, hands behind back in judgment, austere incorruptible bearing. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 李若珪 `minister_li_ruogui.png` — 锦衣卫百户·阉党
```
Li Ruogui, fictional Ming dynasty Jinyiwei centurion, young menacing secret police officer, black flying-fish robe, sword prominently displayed, cold expressionless face, surveillance-trained blank stare, predatory stillness. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed black robes and sword, high detail
```

#### 苏鸣阳 `minister_su_mingyang.png` — 南洋海商代表·西学
```
Su Mingyang, fictional Ming dynasty southern overseas merchant diplomat, wealthy merchant in fine silk robe with subtle foreign trading influences, worldly traveled face, sharp business eyes, holds merchant ledger, cosmopolitan confident bearing. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed merchant robes, high detail
```

#### 张国维 `minister_zhang_guowei.png` — 水利佥事·中立
```
Zhang Guowei, fictional Ming dynasty hydraulic works official, lean practical engineer-official, work-stained robe with mud traces, holds irrigation survey tools, weathered outdoor face, competent problem-solving expression. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes, high detail
```

#### 穆廷弼 `minister_mu_tingbi.png` — 陕西参将·军队
```
Mu Tingbi, fictional Ming dynasty Shaanxi deputy general, rough northwestern military man, heavy campaign armor, fierce scarred face, aggressive battle-ready stance, broad sword at hip, dust of the western frontier on armor. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed campaign armor, high detail
```

#### 贺逢圣 `minister_he_fengsheng.png` — 翰林侍读·东林
```
He Fengsheng, fictional Ming dynasty Hanlin reader-in-waiting, young refined court scholar, elegant pale blue Hanlin robe, delicate bookish features, holding a classical text, thoughtful poetic expression, literary scholar bearing. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and scholar hat, dignified expression, high detail
```

#### 柳如是 `minister_liu_rushi.png` — 江南名士·无党（女）
```
Liu Rushi, fictional Ming dynasty famous Jiangnan female scholar courtesan, remarkable beautiful intelligent woman in scholar's male-style robe with feminine accents, holding calligraphy brush, sharp brilliant eyes, unconventional confident bearing. Ming dynasty portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed scholar robes, high detail
```

#### 谭弘 `minister_tan_hong.png` — 川陕总兵·军队
```
Tan Hong, fictional Ming dynasty Sichuan-Shaanxi garrison general, massive imposing warrior, layered battle armor, fierce battle-tested face, enormous build dwarfing normal men, war axe at side, terrifying battlefield presence. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed heavy armor, high detail
```

#### 郭允厚 `minister_guo_yunhou.png` — 户科给事中·东林
```
Guo Yunhou, fictional Ming dynasty Revenue Scrutiny official, sharp-tongued young official, precise neat robe, accusatory upright posture, finger pointing forward in remonstrance, righteous reform-minded expression. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, high detail
```

#### 范景文 `minister_fan_jingwen.png` — 工部尚书·皇党
```
Fan Jingwen, fictional Ming dynasty Minister of Works loyalist, solid dependable elder official, dignified grey robe, architectural blueprints under arm, measured practical expression, trustworthy administrator bearing. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes and official hat, dignified expression, high detail
```

#### 申用懋 `minister_shen_yongmao.png` — 内阁秘书·阉党
```
Shen Yongmao, fictional Ming dynasty cabinet secretary eunuch-faction, thin servile middle-aged man, grey robe, furtive sidelong glances, secretly taking notes, informer's perpetually suspicious manner. Ming dynasty official full-body portrait, standing pose, hands folded in sleeve, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed robes, high detail
```

#### 邓玉函 `minister_deng_yuhan.png` — 钦天监洋官·西学（耶稣会士）
```
Deng Yuhan Johann Schreck, Jesuit missionary in Ming imperial service, European man in Chinese official robes, round Western face framed by Chinese official hat, holding astronomical globe, blend of East and West in appearance, scholarly curious expression. Ming dynasty official full-body portrait, standing pose, traditional Chinese court painting style, ink wash with light color, white background, vertical composition 3:4, detailed Jesuit-Chinese robes, high detail
```

---

## 开局后宫专属立绘（`consort_<中文名>.png`）

content 开局 5 位后宫**走专属图，不用 pool**：文件名 = `consort_<中文名>.png`
（如 `consort_周皇后.png`）。她们 `portrait_id` 留空，前端按姓名直接取专属图。
风格基底同后宫池（现代古风），命名务必用中文名。

#### 周皇后 `consort_周皇后.png`
```
Empress Zhou, Ming dynasty Chongzhen Emperor chief consort, noble dignified beauty, phoenix crown with nine phoenixes in gold and red jewels, formal yellow-gold dragon-phoenix court robe, erect regal posture, calm authoritative eyes, classic imperial beauty. beautiful young woman with modern aesthetic facial features, refined delicate features, large bright expressive eyes, natural light makeup, flawless porcelain skin, elegant Ming dynasty hanfu, semi-realistic illustration style, soft cinematic lighting, ethereal beauty, ArtStation trending quality, full-body standing portrait, white background, vertical composition 3:4, ultra high detail, masterwork quality
```

#### 田贵妃 `consort_田贵妃.png`
```
Noble Consort Tian, Ming dynasty Chongzhen Emperor most favored consort, luminously beautiful young woman, elaborate gold-and-pearl hair ornament cluster, peach-pink silk robe with intricate peony embroidery, flirtatious confident smile, graceful coquettish pose, undeniable charisma. beautiful young woman with modern aesthetic facial features, refined delicate features, large bright expressive eyes, natural light makeup, flawless porcelain skin, elegant Ming dynasty hanfu, semi-realistic illustration style, soft cinematic lighting, ethereal beauty, ArtStation trending quality, full-body standing portrait, white background, vertical composition 3:4, ultra high detail, masterwork quality
```

#### 袁贵妃 `consort_袁贵妃.png`
```
Noble Consort Yuan, Ming dynasty, poised melancholy beauty, deep crimson formal court robe, simple jade hairpins, downcast introspective eyes, quiet dignified elegance, reserved refined grace. beautiful young woman with modern aesthetic facial features, refined delicate features, large bright expressive eyes, natural light makeup, flawless porcelain skin, elegant Ming dynasty hanfu, semi-realistic illustration style, soft cinematic lighting, ethereal beauty, ArtStation trending quality, full-body standing portrait, white background, vertical composition 3:4, ultra high detail, masterwork quality
```

#### 慧妃 `consort_慧妃.png`
```
Consort Hui, Ming dynasty young imperial concubine, innocent youthful beauty, light sky-blue robe with white plum blossom embroidery, small silver butterfly hairpins, shy gentle smile, hands clasped, fresh morning dew quality. beautiful young woman with modern aesthetic facial features, refined delicate features, large bright expressive eyes, natural light makeup, flawless porcelain skin, elegant Ming dynasty hanfu, semi-realistic illustration style, soft cinematic lighting, ethereal beauty, ArtStation trending quality, full-body standing portrait, white background, vertical composition 3:4, ultra high detail, masterwork quality
```

#### 周贵人 `consort_周贵人.png`
```
Lady Zhou, Ming dynasty imperial concubine of "Worthy Lady" rank, shrewd capable young beauty, sharp perceptive eyes with a knowing look, fair refined features, warm apricot-and-jade silk robe with subtle cloud embroidery, hair in a neat structured bun with pearl and gold hairpins, composed confident half-smile suggesting cleverness and social grace, poised hands, an air of quiet competence and court savvy. beautiful young woman with modern aesthetic facial features, refined delicate features, large bright expressive eyes, natural light makeup, flawless porcelain skin, elegant Ming dynasty hanfu, semi-realistic illustration style, soft cinematic lighting, ethereal beauty, ArtStation trending quality, full-body standing portrait, white background, vertical composition 3:4, ultra high detail, masterwork quality
```

---

## 后宫预设图池（`consort_pool_<N>.png`）

后宫**不做逐角色专属立绘**。妃嫔由大模型在选妃/册封时动态生成（姓名、性情、才艺都是 LLM 发挥），
不可能预先为每个人画图。改用**预设图池**：

- 文件命名 `consort_pool_1.png` … `consort_pool_20.png`（共 20 槽，纯编号，不绑人名）。
- 代码 `db.next_pool_portrait_id("consort_pool_")` 顺序分配下一个空槽给新妃嫔，存入 `portrait_id`。
- 前端按 `portrait_id` 取 `/portraits/consort_pool_<N>.png`，缺图回退占位符。
- content 开局 5 位后宫（周皇后/周贵人/田贵妃/袁贵妃/慧妃）已预绑 `consort_pool_1`–`5`。
- 现有 16 槽（pool 1–16）已出图；**pool 17–20 待补**，照本段风格基底续生即可，主体随意（多样化更好）。

> **要点**：池图只需**风格统一、人物多样**（不同气质/服色/身份），不必对应任何具体妃嫔。
> 越丰富，选妃时撞脸概率越低。

**现代古风风格基底**（每条 prompt 末尾统一加）：

> beautiful young woman with modern aesthetic facial features, refined delicate features, large bright expressive eyes, natural light makeup, flawless porcelain skin, elegant Ming dynasty hanfu, semi-realistic illustration style, soft cinematic lighting, ethereal beauty, ArtStation trending quality, full-body standing portrait, white background, vertical composition 3:4, ultra high detail, masterwork quality

**调性**：保留明制汉服骨架，脸型/妆容/气质走现代审美——精致五官、自然妆感、清爽不浮夸、高颜值。
画风偏唯美插画 / 半写实游戏 CG，弱化古画质感。

---

### 已出图的 16 槽参考主体（按 pool 编号）

> 下列主体即当前 16 张池图的生成依据，留作风格参照与补槽范例。生成时文件名用 `consort_pool_<N>.png`。

#### consort_pool_1（满洲格格·英武明艳）
```
Manchu noblewoman brought to Ming dynasty court, proud striking beauty, bright fierce eyes, fair skin with rosy cheeks, partially sinicized robe mixing Manchu riding dress with Ming court silk, distinctive Manchu hairstyle softened with pearl ornaments, defiant unbroken spirit, athletic elegant figure. [+风格基底]
```

#### consort_pool_2（江湖卖艺·活泼灵动）
```
Lively young acrobat-performer brought to Ming dynasty court, bright energetic beauty, sparkling playful eyes, vivid red and orange performance robe with flowing ribbons and silk sashes, hair in a high bun with colorful streamers, caught mid-dance in a graceful agile pose, joyful radiant smile, charismatic stage presence. [+风格基底]
```

#### consort_pool_3（女侠·飒爽英姿）
```
Female martial artist brought to Ming dynasty court, stunning heroic beauty, sharp bright determined eyes, fitted dark-crimson martial robe with silver trim allowing free movement, high ponytail with a red ribbon, a slender sword at her side, confident agile stance, cool fearless charisma. [+风格基底]
```

#### consort_pool_4（江南名妓·才情风流）
```
Renowned Jiangnan courtesan-poetess at Ming dynasty court, breathtaking cultured beauty, intelligent expressive eyes, elegant pale-lavender silk robe with willow and orchid embroidery, hair half-up with jade and pearl pins, holding a folding fan with calligraphy, refined witty smile, captivating literary charisma. [+风格基底]
```

#### consort_pool_5（才女画师·洒脱灵动）
```
Talented young female painter at Ming dynasty court, fresh artistic beauty, bright clever eyes, a hint of ink smudge on cheek, practical elegant robe in soft green with sleeves rolled, hair loosely pinned with a paintbrush tucked in, holding a fine brush mid-stroke, lively creative free-spirited expression. [+风格基底]
```

#### consort_pool_6（棋待诏·冷静知性）
```
Brilliant young weiqi (Go) master maiden serving Ming dynasty court, cool intelligent beauty, calm analytical eyes, refined deep-indigo robe with subtle geometric embroidery, neat structured hair with a single silver pin, holding a go stone beside a board, composed strategic expression, quiet confident charm. [+风格基底]
```

#### consort_pool_7（道姑·仙气飘逸）
```
Ethereal young Taoist priestess at Ming dynasty court, otherworldly serene beauty, luminous calm eyes, flowing white and pale-blue Taoist robes with cloud-and-crane motifs, simple wooden hairpin and jade pendant, one hand forming a Taoist seal, transcendent dreamy expression, celestial graceful aura. [+风格基底]
```

#### consort_pool_8（写意泼墨·秋色忧郁）
```
Melancholy autumn-themed imperial beauty at Ming dynasty court, poised pensive elegance, deep introspective eyes, amber and russet layered robe with maple-leaf embroidery, jade hairpins, downcast quiet expression, refined seasonal sorrow. [+风格基底]
```

#### consort_pool_9（波斯商女·异域浓彩）
```
Persian merchant's daughter arriving at Ming dynasty court along the Silk Road, exotic Central-West Asian beauty, deep luminous eyes with long lashes, warm olive skin, layered teal and amber silk robe blending Persian textiles with Ming hanfu cut, gold coin headpiece and fine veil, mysterious alluring smile, graceful dancer's poise. [+风格基底]
```

#### consort_pool_10（琴师·清雅文艺）
```
Gifted guqin musician maiden at Ming dynasty court, serene artistic beauty, calm gentle eyes, soft blue-grey flowing robe with cloud-pattern embroidery, simple wooden and jade hairpins, beside a guqin, slender elegant fingers, tranquil absorbed expression, refined musical grace. [+风格基底]
```

#### consort_pool_11（贵妃·工笔淡彩·娇艳）
```
Most-favored imperial noble consort at Ming dynasty court, luminously beautiful young woman, elaborate gold-and-pearl hair ornament cluster, peach-pink silk robe with intricate peony embroidery, flirtatious confident smile, graceful coquettish pose, undeniable charisma. [+风格基底]
```

#### consort_pool_12（女医·温柔聪慧）
```
Gifted young female physician at Ming dynasty court, gentle intelligent beauty, warm caring eyes, practical soft-green robe with herb-pouch and embroidered medical motifs, neat low bun with a jade pin, holding a small medicine box or herb, kind focused healer's expression, reassuring graceful presence. [+风格基底]
```

#### consort_pool_13（东厂女探·暗黑魅惑）
```
Beautiful and dangerous Eastern Depot female spy disguised at Ming dynasty court, alluring sharp beauty, keen vigilant eyes, fitted dark-grey and black palace robe concealing thin blades, sleek high bun with a single dark hairpin, deceptively soft smile masking lethal intent, sleek dangerous elegance, dramatic low-key cinematic lighting. [+风格基底]
```

#### consort_pool_14（皇后/太后·工笔重彩·端庄）
```
Dignified Ming dynasty empress or empress-dowager, noble regal beauty, phoenix crown with gold and red jewels, formal yellow-gold dragon-phoenix court robe, erect regal posture, calm authoritative eyes, classic imperial elegance. [+风格基底]
```

#### consort_pool_15（茶道名媛·温润恬静）
```
Elegant tea-ceremony lady from a scholarly family at Ming dynasty court, warm gentle beauty, soft kind eyes, warm-beige and soft-pink layered robe with plum-blossom embroidery, hair in a graceful low bun with a delicate flower hairpin, holding a celadon teacup, serene welcoming smile, refined gentle poise. [+风格基底]
```

#### consort_pool_16（南洋舶来·海岛风情）
```
Southeast Asian beauty arrived at Ming dynasty court by southern sea trade, exotic island charm, warm golden-brown skin, large dark eyes, glossy black hair with fresh tropical flowers and pearl strands, sea-blue and coral robe blending Nanyang textiles with Ming hanfu, bright friendly smile, slender graceful figure. [+风格基底]
```

#### consort_pool_17–20（待补·自由发挥）
```
留 4 槽。任选不同气质/身份/服色的明制汉服美人，风格基底同上，主体多样即可。
```
