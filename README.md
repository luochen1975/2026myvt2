# 2026myvt2
iptv-actions/
├── .github/workflows/update.yml
├── config/
│   ├── sources.txt          # 多源地址，一行一个
│   └── rules.yaml           # 重命名 + 分组规则（支持你给的 filter 格式）
├── scripts/
│   └── update_iptv.py       # 核心处理脚本
├── output/                  # 自动生成（Actions 会提交）
│   ├── iptv.m3u
│   └── iptv.txt
├── requirements.txt
└── README.md
白名单现在完全可选，留空或写内容都不会删除频道，只靠黑名单剔除。
特殊前缀的源：不测速、不跑 ffprobe，直接进最终列表，并且速度分最高。
组播判断目前包含：rtp://、udp://、包含 2409:8087 的 IPv6。如果你有更多组播特征，告诉我我再加。
