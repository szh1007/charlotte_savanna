# 拉取历史聊天记录的条数
QUERY_HISTORY_LIMIT = 10

# 主体名称阈值 - 确认/候选
# 如何设置: 根据项目环境, 使用大量名称测试集, 测试上限范围和候选范围
# 影响因素: 环境配置 / 是否归一化 / 相似度比较 / 排名器 / 权重比
ITEM_NAME_CONFIRM_THRESHOLD = 0.70
ITEM_NAME_CANDIDATE_THRESHOLD = 0.60
# 主体名称确认/候选的选择数量
# 可以在前端给用户展示候选项
ITEM_NAME_CONFIRM_TOPK = 1
ITEM_NAME_CANDIDATE_TOPK = 2

# RRF 排序融合后的 top_k 数量
MILVUS_CHUNK_RRF_TOP_K = 5
