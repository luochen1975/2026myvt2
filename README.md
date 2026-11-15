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
