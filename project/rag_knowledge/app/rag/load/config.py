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

# 主体识别使用的chunk数量
ITEM_NAME_CONTEXT_CHUNK_K = 5
# 主体识别使用的chunk内容最大总字符数
ITEM_NAME_CONTEXT_TOTAL_MAX_CHARS = 2000

# chunks 批量生成向量的批次大小
EMBEDDING_BATCH_SIZE = 5
