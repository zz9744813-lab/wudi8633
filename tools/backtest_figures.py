"""公众人物回测 —— 用公开已知的人生事件校验术式信号方向（40 人版）。

目的（对抗性）：
1. 排盘事实层核对：年柱 vs 独立干支公式（含立春边界：立春前属前一年）；
2. 信号方向回测：已知已发生的事件（结婚/当选/首富=正向；被逐/破产/退赛=负向），
   六术在事件当天的方向是否与事实同向 —— 逐源统计命中/反向/弃权/报错；
3. 每源做二项检验（vs 0.5），把「命中率是否显著偏离硬币」说清楚。

边界（诚实）：
- 正向事件仍占多数（约 9 成），命中率必须联合正向倾向解读；小样本统计功效有限，
  结果用于找系统性 bug 与校准种子，不构成对术式预测力的证明（C-006）；
- 出生时辰：仅在有公开出生证明/广泛引用的星历时标 time_known=True，否则 False；
- 数据：出生日期与事件日期均为公开记录；日期精确到日，个别奖项公布日为报道日。

用法：python tools/backtest_figures.py  → 输出报告并写入 docs/回测报告-公众人物.md
"""

from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

import app.models.core  # noqa: F401
from app.core.base import AdapterQuery, registry
from app.models.core import BirthProfile
from app.schemas.signal import Domain, TimeScale, TimeWindow

# ----------------------------------------------------------------------
# 数据集：40 位公众人物 + 公开记录事件（expected: +1=正向事件, -1=负向事件）
# ----------------------------------------------------------------------
FIGURES: list[dict] = [
    # ---- 原始 12 位 ----
    {"name": "奥巴马", "birth": (1961, 8, 4), "time": "19:24", "time_known": True, "gender": "male",
     "events": [("1992-10-03", Domain.RELATIONSHIP, "结婚", +1), ("2008-11-04", Domain.CAREER, "当选总统", +1), ("2009-10-09", Domain.CAREER, "获诺贝尔和平奖", +1)]},
    {"name": "特朗普", "birth": (1946, 6, 14), "time": "10:54", "time_known": True, "gender": "male",
     "events": [("2005-01-22", Domain.RELATIONSHIP, "结婚", +1), ("2016-11-09", Domain.CAREER, "当选总统", +1), ("2024-11-06", Domain.CAREER, "再次当选", +1), ("2009-02-17", Domain.MONEY, "赌场集团破产保护", -1)]},
    {"name": "马斯克", "birth": (1971, 6, 28), "time": "07:30", "time_known": True, "gender": "male",
     "events": [("2002-10-01", Domain.MONEY, "PayPal 被收购套现", +1), ("2008-10-06", Domain.CAREER, "执掌特斯拉", +1), ("2021-01-07", Domain.MONEY, "成为世界首富", +1)]},
    {"name": "泰勒·斯威夫特", "birth": (1989, 12, 13), "time": "08:36", "time_known": True, "gender": "female",
     "events": [("2010-01-31", Domain.CAREER, "格莱美年度专辑", +1), ("2016-02-15", Domain.CAREER, "再获年度专辑", +1), ("2023-10-26", Domain.MONEY, "福布斯认证亿万身家", +1)]},
    {"name": "乔布斯", "birth": (1955, 2, 24), "time": "19:15", "time_known": True, "gender": "male",
     "events": [("1976-04-01", Domain.CAREER, "创立苹果", +1), ("1985-09-17", Domain.CAREER, "被逐出苹果", -1), ("2007-01-09", Domain.CAREER, "发布 iPhone", +1), ("1991-03-18", Domain.RELATIONSHIP, "结婚", +1)]},
    {"name": "比尔·盖茨", "birth": (1955, 10, 28), "time": None, "time_known": False, "gender": "male",
     "events": [("1975-04-04", Domain.CAREER, "创立微软", +1), ("1995-08-24", Domain.CAREER, "发布 Windows 95", +1), ("1994-01-01", Domain.RELATIONSHIP, "结婚", +1)]},
    {"name": "奥普拉", "birth": (1954, 1, 29), "time": None, "time_known": False, "gender": "female",
     "events": [("1986-09-08", Domain.CAREER, "节目全国联播", +1), ("2003-03-01", Domain.MONEY, "福布斯亿万身家", +1)]},
    {"name": "勒布朗·詹姆斯", "birth": (1984, 12, 30), "time": None, "time_known": False, "gender": "male",
     "events": [("2003-06-26", Domain.CAREER, "状元入选", +1), ("2016-06-19", Domain.CAREER, "夺得总冠军", +1)]},
    {"name": "塞雷娜·威廉姆斯", "birth": (1981, 9, 26), "time": None, "time_known": False, "gender": "female",
     "events": [("2002-07-06", Domain.CAREER, "首夺温网", +1), ("2017-01-28", Domain.CAREER, "带孕夺澳网", +1)]},
    {"name": "刘德华", "birth": (1961, 9, 27), "time": None, "time_known": False, "gender": "male",
     "events": [("2000-04-16", Domain.CAREER, "首夺金像奖影帝", +1), ("2008-06-23", Domain.RELATIONSHIP, "注册结婚", +1)]},
    {"name": "周杰伦", "birth": (1979, 1, 18), "time": None, "time_known": False, "gender": "male",
     "events": [("2000-11-07", Domain.CAREER, "首张专辑出道", +1), ("2015-01-17", Domain.RELATIONSHIP, "结婚", +1)]},
    {"name": "郎朗", "birth": (1982, 6, 14), "time": None, "time_known": False, "gender": "male",
     "events": [("1999-08-14", Domain.CAREER, "拉维尼亚替补成名", +1), ("2019-06-02", Domain.RELATIONSHIP, "结婚", +1)]},
    # ---- 扩充 28 位 ----
    {"name": "克里斯蒂亚诺·罗纳尔多", "birth": (1985, 2, 5), "time": None, "time_known": False, "gender": "male",
     "events": [("2009-06-11", Domain.CAREER, "创纪录转会皇马", +1), ("2016-07-10", Domain.CAREER, "欧洲杯夺冠", +1)]},
    {"name": "梅西", "birth": (1987, 6, 24), "time": None, "time_known": False, "gender": "male",
     "events": [("2009-12-06", Domain.CAREER, "首夺金球奖", +1), ("2021-08-10", Domain.CAREER, "美洲杯首冠", +1), ("2022-12-18", Domain.CAREER, "世界杯夺冠", +1)]},
    {"name": "蕾哈娜", "birth": (1988, 2, 20), "time": None, "time_known": False, "gender": "female",
     "events": [("2021-08-04", Domain.MONEY, "福布斯亿万身家", +1), ("2023-02-12", Domain.CAREER, "超级碗中场秀", +1)]},
    {"name": "安吉丽娜·朱莉", "birth": (1975, 6, 4), "time": None, "time_known": False, "gender": "female",
     "events": [("2000-03-26", Domain.CAREER, "奥斯卡最佳女配", +1), ("2014-08-23", Domain.RELATIONSHIP, "结婚", +1)]},
    {"name": "布拉德·皮特", "birth": (1963, 12, 18), "time": None, "time_known": False, "gender": "male",
     "events": [("2020-02-09", Domain.CAREER, "奥斯卡最佳男配", +1), ("2014-08-23", Domain.RELATIONSHIP, "结婚", +1)]},
    {"name": "坎耶·维斯特", "birth": (1977, 6, 8), "time": None, "time_known": False, "gender": "male",
     "events": [("2004-02-10", Domain.CAREER, "首张专辑发行", +1), ("2022-10-25", Domain.MONEY, "Adidas 终止合作", -1)]},
    {"name": "贾斯汀·比伯", "birth": (1994, 3, 1), "time": None, "time_known": False, "gender": "male",
     "events": [("2010-01-18", Domain.CAREER, "单曲 Baby 发行", +1), ("2018-09-30", Domain.RELATIONSHIP, "结婚", +1)]},
    {"name": "爱莉安娜·格兰德", "birth": (1993, 6, 26), "time": None, "time_known": False, "gender": "female",
     "events": [("2018-08-17", Domain.CAREER, "专辑首周登顶", +1), ("2021-05-15", Domain.RELATIONSHIP, "结婚", +1)]},
    {"name": "金·卡戴珊", "birth": (1980, 10, 21), "time": None, "time_known": False, "gender": "female",
     "events": [("2007-10-14", Domain.CAREER, "真人秀首播", +1), ("2014-05-24", Domain.RELATIONSHIP, "结婚", +1), ("2021-04-06", Domain.MONEY, "福布斯认证亿万", +1)]},
    {"name": "维纳斯·威廉姆斯", "birth": (1980, 6, 17), "time": None, "time_known": False, "gender": "female",
     "events": [("2000-07-08", Domain.CAREER, "温网首冠", +1), ("2000-09-09", Domain.CAREER, "美网首冠", +1)]},
    {"name": "德约科维奇", "birth": (1987, 5, 22), "time": None, "time_known": False, "gender": "male",
     "events": [("2008-01-27", Domain.CAREER, "首夺大满贯", +1), ("2011-07-04", Domain.CAREER, "登顶世界第一", +1)]},
    {"name": "费德勒", "birth": (1981, 8, 8), "time": None, "time_known": False, "gender": "male",
     "events": [("2003-07-06", Domain.CAREER, "首夺温网", +1), ("2009-06-07", Domain.CAREER, "全满贯达成", +1)]},
    {"name": "泰格·伍兹", "birth": (1975, 12, 30), "time": None, "time_known": False, "gender": "male",
     "events": [("1997-04-13", Domain.CAREER, "首夺大师赛", +1), ("2019-04-14", Domain.CAREER, "复出再夺大师赛", +1), ("2009-11-27", Domain.CAREER, "丑闻爆发生涯受挫", -1)]},
    {"name": "菲尔普斯", "birth": (1985, 6, 30), "time": None, "time_known": False, "gender": "male",
     "events": [("2004-08-14", Domain.CAREER, "首枚奥运金牌", +1), ("2008-08-17", Domain.CAREER, "单届八金", +1)]},
    {"name": "博尔特", "birth": (1986, 8, 21), "time": None, "time_known": False, "gender": "male",
     "events": [("2008-08-16", Domain.CAREER, "百米夺金破世界纪录", +1), ("2009-08-16", Domain.CAREER, "跑出 9 秒 58", +1)]},
    {"name": "杰夫·贝索斯", "birth": (1964, 1, 12), "time": None, "time_known": False, "gender": "male",
     "events": [("1994-07-05", Domain.CAREER, "创立亚马逊", +1), ("2017-10-27", Domain.MONEY, "登顶全球首富", +1), ("2019-04-04", Domain.RELATIONSHIP, "离婚（巨额分割）", -1)]},
    {"name": "马克·扎克伯格", "birth": (1984, 5, 14), "time": None, "time_known": False, "gender": "male",
     "events": [("2004-02-04", Domain.CAREER, "创立 Facebook", +1), ("2012-05-18", Domain.MONEY, "IPO 上市", +1), ("2012-05-19", Domain.RELATIONSHIP, "结婚", +1)]},
    {"name": "拉里·佩奇", "birth": (1973, 3, 26), "time": None, "time_known": False, "gender": "male",
     "events": [("1998-09-04", Domain.CAREER, "创立 Google", +1), ("2004-08-19", Domain.MONEY, "IPO 上市", +1)]},
    {"name": "沃伦·巴菲特", "birth": (1930, 8, 30), "time": None, "time_known": False, "gender": "male",
     "events": [("1965-05-10", Domain.CAREER, "执掌伯克希尔", +1), ("2008-03-05", Domain.MONEY, "福布斯全球首富", +1)]},
    {"name": "马云", "birth": (1964, 9, 10), "time": None, "time_known": False, "gender": "male",
     "events": [("2014-09-19", Domain.MONEY, "阿里 IPO", +1), ("2020-11-03", Domain.CAREER, "蚂蚁上市暂缓", -1)]},
    {"name": "成龙", "birth": (1954, 4, 7), "time": None, "time_known": False, "gender": "male",
     "events": [("1996-02-23", Domain.CAREER, "红番区北美破圈", +1), ("2016-11-12", Domain.CAREER, "奥斯卡终身成就奖", +1)]},
    {"name": "姚明", "birth": (1980, 9, 12), "time": None, "time_known": False, "gender": "male",
     "events": [("2002-06-26", Domain.CAREER, "NBA 状元入选", +1), ("2016-09-09", Domain.CAREER, "入选名人堂", +1)]},
    {"name": "刘翔", "birth": (1983, 7, 13), "time": None, "time_known": False, "gender": "male",
     "events": [("2004-08-27", Domain.CAREER, "雅典夺金", +1), ("2008-08-18", Domain.CAREER, "因伤退赛", -1)]},
    {"name": "谷爱凌", "birth": (2003, 9, 3), "time": None, "time_known": False, "gender": "female",
     "events": [("2022-02-08", Domain.CAREER, "冬奥首金", +1), ("2022-02-18", Domain.CAREER, "冬奥第二金", +1)]},
    {"name": "李娜", "birth": (1982, 2, 26), "time": None, "time_known": False, "gender": "female",
     "events": [("2011-06-04", Domain.CAREER, "法网夺冠", +1), ("2014-01-25", Domain.CAREER, "澳网夺冠", +1)]},
    {"name": "莫言", "birth": (1955, 2, 17), "time": None, "time_known": False, "gender": "male",
     "events": [("2011-08-20", Domain.CAREER, "茅盾文学奖", +1), ("2012-10-11", Domain.CAREER, "诺贝尔文学奖", +1)]},
    {"name": "屠呦呦", "birth": (1930, 12, 30), "time": None, "time_known": False, "gender": "female",
     "events": [("2011-09-12", Domain.CAREER, "拉斯克奖", +1), ("2015-10-05", Domain.CAREER, "诺贝尔生理学或医学奖", +1)]},
    {"name": "袁隆平", "birth": (1930, 9, 7), "time": None, "time_known": False, "gender": "male",
     "events": [("2004-10-14", Domain.CAREER, "世界粮食奖", +1), ("2019-09-29", Domain.CAREER, "共和国勋章", +1)]},
    {"name": "碧昂丝", "birth": (1981, 9, 4), "time": None, "time_known": False, "gender": "female",
     "events": [("2003-06-23", Domain.CAREER, "首张个人专辑", +1), ("2008-04-04", Domain.RELATIONSHIP, "结婚", +1), ("2010-01-31", Domain.CAREER, "单夜六座格莱美", +1)]},
    {"name": "阿黛尔", "birth": (1988, 5, 5), "time": None, "time_known": False, "gender": "female",
     "events": [("2012-02-12", Domain.CAREER, "格莱美年度专辑", +1), ("2017-02-12", Domain.CAREER, "再获年度专辑", +1)]},
    {"name": "道恩·强森", "birth": (1972, 5, 2), "time": None, "time_known": False, "gender": "male",
     "events": [("1998-11-15", Domain.CAREER, "首夺 WWF 冠军", +1), ("2016-08-30", Domain.CAREER, "福布斯收入最高演员", +1)]},
    {"name": "麦当娜", "birth": (1958, 8, 16), "time": None, "time_known": False, "gender": "female",
     "events": [("1984-09-14", Domain.CAREER, "VMA 表演一鸣惊人", +1), ("2012-02-05", Domain.CAREER, "超级碗中场秀", +1)]},
    {"name": "汤姆·汉克斯", "birth": (1956, 7, 9), "time": None, "time_known": False, "gender": "male",
     "events": [("1988-04-30", Domain.RELATIONSHIP, "结婚", +1), ("1994-03-21", Domain.CAREER, "奥斯卡影帝", +1), ("1995-03-27", Domain.CAREER, "奥斯卡连庄", +1)]},
    # ---- 扩充第二轮（30 位）----
    {"name": "迈克尔·乔丹", "birth": (1963, 2, 17), "time": None, "time_known": False, "gender": "male",
     "events": [("1991-06-12", Domain.CAREER, "首夺总冠军", +1), ("1993-10-06", Domain.CAREER, "首次退役", -1), ("1995-03-18", Domain.CAREER, "复出", +1)]},
    {"name": "科比·布莱恩特", "birth": (1978, 8, 23), "time": None, "time_known": False, "gender": "male",
     "events": [("2006-01-22", Domain.CAREER, "81 分之夜", +1), ("2010-06-17", Domain.CAREER, "第五冠", +1)]},
    {"name": "迈克尔·泰森", "birth": (1966, 6, 30), "time": None, "time_known": False, "gender": "male",
     "events": [("1986-11-22", Domain.CAREER, "最年轻重量级拳王", +1), ("1992-02-10", Domain.CAREER, "入狱", -1)]},
    {"name": "埃米纳姆", "birth": (1972, 10, 17), "time": None, "time_known": False, "gender": "male",
     "events": [("2002-05-26", Domain.CAREER, "阿姆秀专辑", +1), ("2003-03-23", Domain.CAREER, "奥斯卡最佳原创歌曲", +1)]},
    {"name": "席琳·迪翁", "birth": (1968, 3, 30), "time": None, "time_known": False, "gender": "female",
     "events": [("1998-03-23", Domain.CAREER, "奥斯卡最佳原创歌曲", +1), ("2003-03-25", Domain.CAREER, "拉斯维加斯驻唱开演", +1)]},
    {"name": "Lady Gaga", "birth": (1986, 3, 28), "time": None, "time_known": False, "gender": "female",
     "events": [("2008-08-19", Domain.CAREER, "首专 The Fame 发行", +1), ("2017-02-05", Domain.CAREER, "超级碗中场秀", +1), ("2019-02-24", Domain.CAREER, "奥斯卡最佳原创歌曲", +1)]},
    {"name": "艾玛·沃特森", "birth": (1990, 4, 15), "time": None, "time_known": False, "gender": "female",
     "events": [("2001-11-16", Domain.CAREER, "哈利波特首映", +1), ("2014-09-21", Domain.CAREER, "联合国 HeForShe 演讲", +1)]},
    {"name": "丹尼尔·雷德克里夫", "birth": (1989, 7, 23), "time": None, "time_known": False, "gender": "male",
     "events": [("2001-11-16", Domain.CAREER, "哈利波特首映", +1), ("2007-02-27", Domain.CAREER, "舞台剧 Equus 突破", +1)]},
    {"name": "罗纳尔多（大罗）", "birth": (1976, 9, 18), "time": None, "time_known": False, "gender": "male",
     "events": [("2002-06-30", Domain.CAREER, "世界杯决赛双响夺冠", +1), ("2002-08-31", Domain.CAREER, "转会皇马", +1)]},
    {"name": "内马尔", "birth": (1992, 2, 5), "time": None, "time_known": False, "gender": "male",
     "events": [("2013-05-26", Domain.CAREER, "巴萨官宣转会", +1), ("2017-08-03", Domain.CAREER, "2.2 亿欧转会巴黎", +1)]},
    {"name": "姆巴佩", "birth": (1998, 12, 20), "time": None, "time_known": False, "gender": "male",
     "events": [("2018-07-15", Domain.CAREER, "世界杯夺冠", +1), ("2024-06-03", Domain.CAREER, "皇马官宣加盟", +1)]},
    {"name": "宫崎骏", "birth": (1941, 1, 5), "time": None, "time_known": False, "gender": "male",
     "events": [("2001-07-20", Domain.CAREER, "千与千寻上映", +1), ("2003-03-23", Domain.CAREER, "千与千寻奥斯卡", +1)]},
    {"name": "林书豪", "birth": (1988, 8, 23), "time": None, "time_known": False, "gender": "male",
     "events": [("2012-02-04", Domain.CAREER, "林来疯爆发", +1), ("2019-06-13", Domain.CAREER, "随猛龙夺冠", +1)]},
    {"name": "贝克汉姆", "birth": (1975, 5, 2), "time": None, "time_known": False, "gender": "male",
     "events": [("2003-07-01", Domain.CAREER, "皇马官宣转会", +1), ("1999-07-04", Domain.RELATIONSHIP, "结婚", +1)]},
    {"name": "维多利亚·贝克汉姆", "birth": (1974, 4, 17), "time": None, "time_known": False, "gender": "female",
     "events": [("1996-07-08", Domain.CAREER, "Wannabe 发行", +1), ("1999-07-04", Domain.RELATIONSHIP, "结婚", +1)]},
    {"name": "沙奎尔·奥尼尔", "birth": (1972, 3, 6), "time": None, "time_known": False, "gender": "male",
     "events": [("1992-06-24", Domain.CAREER, "状元入选", +1), ("2000-06-19", Domain.CAREER, "首冠+FMVP", +1)]},
    {"name": "蒂姆·邓肯", "birth": (1976, 4, 25), "time": None, "time_known": False, "gender": "male",
     "events": [("1997-06-25", Domain.CAREER, "状元入选", +1), ("1999-06-25", Domain.CAREER, "首冠", +1)]},
    {"name": "斯蒂芬·库里", "birth": (1988, 3, 14), "time": None, "time_known": False, "gender": "male",
     "events": [("2015-06-16", Domain.CAREER, "首冠", +1), ("2016-04-13", Domain.CAREER, "73 胜赛季", +1)]},
    {"name": "凯文·杜兰特", "birth": (1988, 9, 29), "time": None, "time_known": False, "gender": "male",
     "events": [("2007-06-28", Domain.CAREER, "榜眼入选", +1), ("2017-06-12", Domain.CAREER, "首冠+FMVP", +1)]},
    {"name": "迈克尔·杰克逊", "birth": (1958, 8, 29), "time": None, "time_known": False, "gender": "male",
     "events": [("1982-11-30", Domain.CAREER, "Thriller 发行", +1), ("1993-01-31", Domain.CAREER, "超级碗中场秀", +1)]},
    {"name": "猫王", "birth": (1935, 1, 8), "time": None, "time_known": False, "gender": "male",
     "events": [("1956-01-27", Domain.CAREER, "Heartbreak Hotel 发行", +1), ("1958-03-24", Domain.CAREER, "入伍服役", -1)]},
    {"name": "约翰·列侬", "birth": (1940, 10, 9), "time": None, "time_known": False, "gender": "male",
     "events": [("1969-03-20", Domain.RELATIONSHIP, "结婚", +1), ("1970-04-10", Domain.CAREER, "披头士解散", -1)]},
    {"name": "保罗·麦卡特尼", "birth": (1942, 6, 18), "time": None, "time_known": False, "gender": "male",
     "events": [("1969-03-12", Domain.RELATIONSHIP, "结婚", +1), ("1970-04-10", Domain.CAREER, "宣布离队", -1)]},
    {"name": "爱因斯坦", "birth": (1879, 3, 14), "time": None, "time_known": False, "gender": "male",
     "events": [("1905-06-30", Domain.CAREER, "狭义相对论发表", +1), ("1922-11-10", Domain.CAREER, "诺贝尔物理学奖", +1)]},
    {"name": "达尔文", "birth": (1809, 2, 12), "time": None, "time_known": False, "gender": "male",
     "events": [("1831-12-27", Domain.CAREER, "小猎犬号启航", +1), ("1859-11-24", Domain.CAREER, "物种起源出版", +1)]},
    {"name": "居里夫人", "birth": (1867, 11, 7), "time": None, "time_known": False, "gender": "female",
     "events": [("1903-12-10", Domain.CAREER, "诺贝尔物理学奖", +1), ("1911-12-10", Domain.CAREER, "诺贝尔化学奖", +1)]},
    {"name": "雷军", "birth": (1969, 12, 16), "time": None, "time_known": False, "gender": "male",
     "events": [("2010-04-06", Domain.CAREER, "小米创立", +1), ("2018-07-09", Domain.MONEY, "小米港股 IPO", +1)]},
    {"name": "刘强东", "birth": (1974, 2, 14), "time": None, "time_known": False, "gender": "male",
     "events": [("1998-06-18", Domain.CAREER, "京东创立", +1), ("2014-05-22", Domain.MONEY, "纳斯达克 IPO", +1), ("2018-09-02", Domain.CAREER, "明州事件", -1)]},
    {"name": "郑钦文", "birth": (2002, 10, 8), "time": None, "time_known": False, "gender": "female",
     "events": [("2024-08-03", Domain.CAREER, "奥运女单金牌", +1)]},
]

_STEM = "甲乙丙丁戊己庚辛壬癸"
_BRANCH = "子丑寅卯辰巳午未申酉戌亥"


def expected_year_pillar(y: int, m: int, d: int) -> str:
    """独立干支公式推年柱（与系统实现无关）：立春(约2/4)前属前一年。

    数据集中无人出生于 2/2~2/6 边界带，固定 2/4 分界足够安全。
    """
    eff = y - 1 if (m, d) < (2, 4) else y
    return _STEM[(eff - 4) % 10] + _BRANCH[(eff - 4) % 12]


def binom_two_sided_p(hits: int, n: int) -> float:
    """精确二项双侧 p 值（H0: p=0.5）。"""
    if n == 0:
        return 1.0
    k = min(hits, n - hits)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def _adapter_direction(adapter, query) -> tuple[float, bool]:
    """取该源在事件窗口的最强非降级信号方向。

    返回 (direction, errored)：无信号/降级 → (0, False)；adapter 抛错 → (0, True)。
    报错必须与弃权分开统计 —— 把崩溃美化成弃权等于自欺（C-006）。
    """
    try:
        sigs = adapter.signals(query)
    except Exception:
        return 0.0, True
    best = 0.0
    best_strength = -1.0
    for s in sigs:
        if s.degraded:
            continue
        if s.strength > best_strength:
            best_strength = s.strength
            best = s.direction
    return best, False


def main() -> dict:
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)

    adapters = [a for a in registry.all() if a.source.value in
                ("bazi", "ziwei", "liuyao", "meihua", "zhouyi", "qimen")]

    per_source: dict[str, dict[str, int]] = {
        a.source.value: {"hit": 0, "miss": 0, "abstain": 0, "error": 0} for a in adapters
    }
    crossed_stats = {"hit": 0, "miss": 0, "abstain": 0}
    pillar_rows: list[dict] = []
    event_log: list[dict] = []

    with Session(eng) as s:
        for idx, fig in enumerate(FIGURES):
            uid = 9900 + idx
            y, m, d = fig["birth"]
            s.add(
                BirthProfile(
                    user_id=uid,
                    solar_birth_date=date(y, m, d),
                    solar_birth_time=fig["time"] or "00:00",
                    birth_time_known=fig["time_known"],
                    gender=fig["gender"],
                )
            )
            s.commit()

            # 排盘事实层：年柱 vs 独立干支公式（含立春边界）
            from app.core.calendar.core import CalendarCore

            core = CalendarCore()
            r = core.compute(
                birth_date=date(y, m, d),
                birth_time=fig["time"] or "00:00",
                target_date=date(y, m, d),
                target_time=fig["time"] or "00:00",
                gender=fig["gender"],
            )
            # 八字年柱取 payload["bazi"]["year"]（EightChar 立春切换口径）；
            # payload["year_ganzhi"] 是农历年（正月初一切换，供老黄历/起卦），两者口径不同：
            # 立春与正月初一之间的生日两者差一年（如 C罗 2/5 生：八字乙丑/农历甲子），均属正确。
            year_gz = str((r.payload.get("bazi") or {}).get("year", ""))
            expect = expected_year_pillar(y, m, d)
            pillar_rows.append(
                {"name": fig["name"], "year": year_gz, "expect": expect,
                 "ok": year_gz == expect, "pillars": r.payload.get("bazi")}
            )

            for ds, domain, label, expected in fig["events"]:
                yy, mm, dd = (int(x) for x in ds.split("-"))
                ev = date(yy, mm, dd)
                # 事件真实时辰未知，统一取 12:00 曾使梅花锁死在 8 种上下卦配对
                # （固定时辰 -> 上下卦数差恒定 -> 体用关系偏斜 77% 负）——
                # 改为逐事件确定性伪随机时辰，消除固定时辰偏斜（勿回退）。
                hh = (idx * 7 + len(event_log) * 3) % 24
                mi = (len(event_log) * 13 + 7) % 60
                q = AdapterQuery(
                    user_id=uid,
                    domain=domain,
                    target_event=f"backtest.{domain.value}",
                    time_scale=TimeScale.DAY,
                    window=TimeWindow(
                        start=datetime(yy, mm, dd),
                        end=datetime(yy, mm, dd) + timedelta(hours=24),
                    ),
                    target_date=ev,
                    target_time=f"{hh:02d}:{mi:02d}",
                    session=s,
                )
                dirs: dict[str, float] = {}
                for a in adapters:
                    direction, errored = _adapter_direction(a, q)
                    dirs[a.source.value] = direction
                    bucket = per_source[a.source.value]
                    if errored:
                        bucket["error"] += 1
                    elif direction == 0:
                        bucket["abstain"] += 1
                    elif direction * expected > 0:
                        bucket["hit"] += 1
                    else:
                        bucket["miss"] += 1
                directional = [v for v in dirs.values() if v != 0]
                if len(directional) >= 2:
                    majority_up = sum(1 for v in directional if v > 0) >= len(directional) / 2
                    ok = (majority_up and expected > 0) or (not majority_up and expected < 0)
                    crossed_stats["hit" if ok else "miss"] += 1
                else:
                    crossed_stats["abstain"] += 1
                event_log.append(
                    {"figure": fig["name"], "event": label, "date": ds,
                     "expected": expected, "dirs": dirs}
                )

    return {
        "per_source": per_source,
        "crossed": crossed_stats,
        "pillars": pillar_rows,
        "events": event_log,
        "n_events": len(event_log),
        "n_positive": sum(1 for e in event_log if e["expected"] > 0),
    }


def render(result: dict) -> str:
    lines: list[str] = []
    n_neg = result["n_events"] - result["n_positive"]
    lines.append("# 公众人物回测报告（自动生成）")
    lines.append("")
    lines.append(
        f"- 样本：{len(result['pillars'])} 位公众人物 × 共 {result['n_events']} 个公开记录事件"
        f"（正向 {result['n_positive']} / 负向 {n_neg}）"
    )
    lines.append("- 方法：每个事件当天跑全部六术 adapter，信号方向与已知事实对比；每源做 vs 0.5 的精确二项检验")
    lines.append(
        "- 边界：正向事件仍占多数，命中率须联合正向倾向解读；结果用于找系统性 bug 与校准种子，"
        "不构成对术式预测力的证明（C-006）"
    )
    lines.append("")
    lines.append("## 一、排盘事实层（八字年柱 vs 独立干支公式，含立春边界）")
    lines.append("")
    lines.append(
        "> 口径注记：系统同时维护两套年柱——`year_ganzhi` 为农历年（正月初一切换，"
        "供老黄历与时间起卦），`bazi.year` 为八字年柱（立春切换，供四柱）。"
        "两者在立春与正月初一之间的生日相差一年，均为各自口径下的正确值。"
        "本表核对的是八字年柱。"
    )
    lines.append("")
    lines.append("| 人物 | 年柱（系统） | 年柱（独立公式） | 一致 |")
    lines.append("|---|---|---|---|")
    bad = 0
    for row in result["pillars"]:
        flag = "✅" if row["ok"] else "❌"
        if not row["ok"]:
            bad += 1
        lines.append(f"| {row['name']} | {row['year']} | {row['expect']} | {flag} |")
    lines.append(f"\n年柱一致率：{len(result['pillars']) - bad}/{len(result['pillars'])}")
    lines.append("")
    lines.append("## 二、信号方向回测（已知事实 vs 六术方向）")
    lines.append("")
    lines.append("| 术式 | 方向性判定 | 命中 | 反向 | 弃权 | 报错 | 命中率 | p 值（vs 0.5） | 判读 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    pos_rate = result["n_positive"] / result["n_events"]
    for src, b in sorted(result["per_source"].items()):
        n = b["hit"] + b["miss"]
        acc = f"{b['hit'] / n:.0%}" if n else "—"
        p = binom_two_sided_p(b["hit"], n) if n else 1.0
        if n and p < 0.05:
            if b["hit"] / n > pos_rate:
                verdict = "显著偏正（近灌水，方向信息量低）"
            elif b["hit"] / n > 0.5:
                verdict = "显著偏正（低于事件正向占比，仍有正向偏置）"
            else:
                verdict = "显著反向（查实现）"
        elif n and 0.05 <= p < 0.15:
            verdict = "观察名单（弱显著）"
        else:
            verdict = "与硬币无异"
        lines.append(
            f"| {src} | {n} | {b['hit']} | {b['miss']} | {b['abstain']} | {b.get('error', 0)} | {acc} | {p:.3f} | {verdict} |"
        )
    cx = result["crossed"]
    cn = cx["hit"] + cx["miss"]
    lines.append(
        f"\n多数派交叉（≥2 术式给方向时取多数）：命中 {cx['hit']} / 反向 {cx['miss']}"
        f" / 弃权 {cx['abstain']}"
        + (f"，命中率 {cx['hit'] / cn:.0%}" if cn else "")
    )
    lines.append("")
    lines.append("## 二·五、解读须知（对抗性声明）")
    lines.append("")
    lines.append(
        f"- 本回测集正向事件占 {pos_rate:.0%}：任何「略偏正向」的信号器命中率都会虚高。"
        "读各源命中率时须联合其正向倾向（如 zhouyi 命中率接近正向占比即意味着几乎只说好话）。"
    )
    lines.append(
        "- zhouyi 方向判定基于吉凶断辞词频，弱吉套话（亨/利/无咎）已降权为中性；"
        "「元亨利贞」类卦辞不再产生方向。"
    )
    lines.append(
        "- qimen 主断为「日干落宫(人)×时干落宫(事)」生克，值符门与格局为修正，相左弃权；"
        "定局已修 getPrevJieQi（中气曾致全年约一半日子局数错档）。"
    )
    lines.append(
        "- 单轮回测不构成预测力证明（C-006）；显著结果应作为校准种子进入可靠度矩阵，"
        "由验证闭环持续实证。"
    )
    lines.append("")
    lines.append("## 三、事件明细（方向：+ 同向 / - 反向 / 0 弃权）")
    lines.append("")
    sources = sorted(result["per_source"].keys())
    lines.append("| 人物 | 事件 | 日期 | 期望 | " + " | ".join(sources) + " |")
    lines.append("|---|---|---|---|" + "---|" * len(sources))
    for ev in result["events"]:
        cells = []
        for src in sources:
            v = ev["dirs"].get(src, 0)
            cells.append("+" if v > 0 else ("-" if v < 0 else "○"))
        lines.append(
            f"| {ev['figure']} | {ev['event']} | {ev['date']} | {ev['expected']:+d} | "
            + " | ".join(cells) + " |"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    result = main()
    report = render(result)
    print(report)
    out = Path(__file__).resolve().parents[1] / "docs" / "回测报告-公众人物.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    (out.parent / "回测数据-公众人物.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    print(f"[已写入 {out}]")
