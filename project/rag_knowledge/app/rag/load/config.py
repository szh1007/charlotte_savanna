# Markdown 中支持分析的图片扩展名
SUPPORTED_IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".gif", ".webp"]

# Markdown 中引用图片的上下文截取的字符数
IMAGE_CONTEXT_SUB_CHARS = 100

# 最大切块长度
CHUNK_MAX_SIZE = 1000
# 基准切块长度
CHUNK_SIZE = 600
# 切块重叠长度
CHUNK_OVERLAP = 50
# 最小碎片阈值
CHUNK_MIN = 400
# 跨节合并的判据: 前一块的「正文」(剥掉标题行) 短于这个数, 才算需要被吸收的碎片.
# 带 3-4 行说明的小节整块 150 字、正文 130 字, 是能独立检索的正常块.
# 判据放宽会把正常小节也吞进来, 一个块里混两个主题, 谁的问题都匹配不好.
CROSS_MERGE_BODY_MIN = 80

# 主体识别使用的chunk数量
ITEM_NAME_CONTEXT_CHUNK_K = 5
# 主体识别使用的chunk内容最大总字符数
ITEM_NAME_CONTEXT_TOTAL_MAX_CHARS = 2000

# chunks 批量生成向量的批次大小
EMBEDDING_BATCH_SIZE = 5
