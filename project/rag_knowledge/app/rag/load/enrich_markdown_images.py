import base64
import re
from mimetypes import guess_type
from pathlib import Path

from langchain.messages import HumanMessage
from langchain_core.output_parsers import StrOutputParser
from minio.deleteobjects import DeleteObject

from ...infra.config import infra_config
from ...infra.minio import infra_minio
from ...infra.model import infra_model
from ...process.load.agent.state import LoadState
from ...shared.runtime.load_prompt import load_prompt
from ...shared.runtime.logger import logger, step_log
from ...shared.utils.rate_limit_utils import apply_api_rate_limit
from .config import IMAGE_CONTEXT_SUB_CHARS, SUPPORTED_IMAGE_EXTENSIONS


@step_log("enrich_markdown_images")
def enrich_markdown_images(state: LoadState) -> LoadState:
    md_path_obj, md_images_dir_obj, md_content = _validate_data(state)

    if not md_images_dir_obj.is_dir() or len(list(md_images_dir_obj.iterdir())) == 0:
        logger.info(f"图片目录不存在/为空, 无需处理直接跳过: {md_images_dir_obj}")
        return state

    image_info_list = _scan_images(md_path_obj, md_content, md_images_dir_obj)
    summary_image_dict = _summarize_images(md_path_obj, image_info_list)
    image_url_dict = _upload_images_get_url(md_path_obj, image_info_list)
    new_md_content = _md_content_image_replace(
        md_path_obj, md_content, image_url_dict, summary_image_dict
    )
    md_path_obj_new: Path = _backup_new_md_content(md_path_obj, new_md_content)

    state["md_path"] = str(md_path_obj_new)
    state["md_content"] = new_md_content
    return state


@step_log("_validate_data")
def _validate_data(state: LoadState) -> tuple[Path, Path, str]:
    """
    校验并获取必备的参数

    Args:
        state: 加载状态字典
    Returns:
        md_path_obj: Markdown 文件路径对象, 用于后续获取文件名
        md_images_dir_obj: Markdown 图片目录路径对象, 用于后续获取图片
        md_content: 未经处理过的 Markdown 文件内容
    """
    md_path = state.get("md_path")
    if not md_path:
        logger.error(f"md_path 参数为空: {md_path}")
        raise ValueError(f"md_path 参数为空: {md_path}")

    md_path_obj = Path(md_path)
    if not md_path_obj.is_file():
        logger.error(f"文件不存在/不是文件: {md_path_obj}")
        raise ValueError(f"文件不存在/不是文件: {md_path_obj}")

    md_content = md_path_obj.read_text(encoding="utf-8")
    state["md_content"] = md_content

    md_images_dir_obj = md_path_obj.parent / "images"

    return md_path_obj, md_images_dir_obj, md_content


@step_log("_scan_images")
def _scan_images(
    md_path_obj: Path, md_content: str, md_images_dir_obj: Path
) -> list[tuple[str, str, tuple[str, str]]]:
    """
    遍历 Markdown 文件内容中使用的文件, 及其上下文信息

    Args:
        md_path_obj: Markdown 文件路径对象
        md_content: 未经处理过的 Markdown 文件内容
        md_images_dir_obj: Markdown 图片目录路径对象
    Returns:
        image_info_list: 图片信息列表, 每个元素为 (图片名, 图片路径, (前文, 后文))
    """
    image_info_list = []
    for image_file_obj in md_images_dir_obj.iterdir():
        image_name: str = image_file_obj.name
        image_path: str = str(image_file_obj)

        # 1.检查是否为图片
        if image_file_obj.suffix not in SUPPORTED_IMAGE_EXTENSIONS:
            logger.warning(f"当前处理的文件不是可支持的图片: {image_name}")
            continue

        # 2.匹配该图片是否出现在 md_content 中
        reg = re.compile(r"\!\[.*?\]\(.*?" + re.escape(image_name) + r".*?\)")
        search_match = reg.search(md_content)
        if not search_match:
            logger.warning(f"{image_name} 未被Markdown文件引用")
            continue
        start, end = search_match.start(), search_match.end()

        # 3.截取上下文
        pre_context = md_content[max(0, start - IMAGE_CONTEXT_SUB_CHARS) : start]
        post_context = md_content[
            end : min(end + IMAGE_CONTEXT_SUB_CHARS, len(md_content))
        ]
        logger.debug(f"{image_name} 被Markdown文件引用, 引用位置索引: {start}-{end}")

        # 4.添加到列表
        image_info_list.append((image_name, image_path, (pre_context, post_context)))

    logger.info(
        f"{md_path_obj!s} 所有引用的图片及上下文信息已经识别完毕, "
        f"数量: {len(image_info_list)}"
    )
    return image_info_list


@step_log("_summarize_images")
def _summarize_images(
    md_path_obj: Path,
    image_info_list: list[tuple[str, str, tuple[str, str]]],
) -> dict[str, str]:
    """视觉模型识别图片含义"""
    summary_image_dict: dict[str, str] = {}

    # 1.定义视觉模型
    vision_model = infra_model.vision_model(infra_config.llm_config.vl_model)

    # 2.遍历 image_info_list
    for image_name, image_path, pre_post in image_info_list:
        # 3.加载并拼接视觉模型提示词
        image_prompt_text = load_prompt(
            name="image_summary",  # 提示词文件名
            root_folder=md_path_obj.stem,  # Markdown 文件所在根目录名称
            image_content=pre_post,  # 图片上下文信息
        )

        # 4.base64 编码图片
        image_base64 = base64.b64encode(Path(image_path).read_bytes())
        image_base64_str = image_base64.decode(encoding="utf-8")
        mimetype = guess_type(image_name)[0]

        # 5.拼接 messages
        messages = HumanMessage(
            content=[
                {"type": "text", "text": image_prompt_text},  # 文本格式
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mimetype};base64,{image_base64_str}"},
                },  # 图片格式
            ]
        )

        # 6.调用视觉模型获取结果
        chains = vision_model | StrOutputParser()

        # 7.添加范围内限制
        apply_api_rate_limit()

        # 8.结果拼接到字典数据中
        image_summary = chains.invoke([messages])
        summary_image_dict[image_name] = image_summary
        logger.debug(f"视觉识别完成: {image_name}, 识别结果: {image_summary}")

    return summary_image_dict


@step_log("_upload_images_get_url")
def _upload_images_get_url(
    md_path_obj: Path,
    image_info_list: list[tuple[str, str, tuple[str, str]]],
) -> dict[str, str]:
    """上传文件, 并获取文件的访问URL"""

    minio_client = infra_minio.client

    # 1.删除同名目录下的所有objects
    # 获取所有objects
    list_object = minio_client.list_objects(
        bucket_name=infra_minio.bucket_name,
        prefix=f"{infra_minio.image_dir}/{md_path_obj.stem}/",  # 开头绝对不能带 /
        recursive=True,
    )
    # 给所有objects打上删除标记
    delete_object_list = [DeleteObject(obj.object_name) for obj in list_object]

    # 实施删除动作 (返回迭代器)
    errors = minio_client.remove_objects(
        bucket_name=infra_minio.bucket_name,  # 指定桶名
        delete_object_list=delete_object_list,  # 打上删除标记的objects
    )
    # 遍历迭代器才会执行删除
    for error in errors:
        logger.warning(f"图片删除报错: {error}")

    # 2.重新上传本次对应的文件
    image_url_dict: dict[str, str] = {}
    for image_name, image_path, _ in image_info_list:
        try:
            minio_client.fput_object(
                bucket_name=infra_minio.bucket_name,
                object_name=f"{infra_minio.image_dir}/{md_path_obj.stem}/{image_name}",
                file_path=image_path,
                content_type=guess_type(image_name)[0],
            )
            url = infra_minio.build_image_url(md_path_obj.stem, image_name)
            image_url_dict[image_name] = url
            logger.debug(f"上传成功: {image_name}, 地址: {url}")
        except Exception:
            logger.warning(f"上传失败: {image_name}, 先跳过, 继续上传下一张图片")

    return image_url_dict


@step_log("_md_content_image_replace")
def _md_content_image_replace(
    md_path_obj: Path,
    md_content: str,
    image_url_dict: dict[str, str],
    summary_image_dict: dict[str, str],
) -> str:
    """
    替换Markdown文件中的图片引用, 用图片概述和Minio的URL替换

    Args:
        md_path_obj: Markdown文件路径
        md_content(old): Markdown文件内容 (替换图片信息前)
        image_url_dict: {Markdown文件名: 图片Minio路径}
        summary_image_dict: {Markdown文件名: 图片概述}
    Returns:
        md_content(new): Markdown文件内容 (替换图片信息后)
    """
    for name, summary in summary_image_dict.items():
        url = image_url_dict.get(name)
        summary = summary.replace("\n", "")

        reg = re.compile(r"\!\[.*?\]\(.*?" + re.escape(name) + r".*?\)")
        md_content = reg.sub(lambda _: f"![{summary}]({url})", md_content)

        logger.debug(f"{md_path_obj.name} 图片信息替换: {name} -> {url}: {summary}")
    return md_content


@step_log("_backup_new_md_content")
def _backup_new_md_content(md_path_obj: Path, new_md_content: str) -> Path:
    """
    new_md_content 备份
    xxx.md -> xxx_new.md
    """
    md_path_obj_new: Path = md_path_obj.with_name(f"{md_path_obj.stem}_new.md")
    md_path_obj_new.write_text(data=new_md_content, encoding="utf-8")
    logger.info(f"新Markdown文件内容已备份至: {md_path_obj_new!s}")
    return md_path_obj_new
