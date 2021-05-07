__all__ = ["BaseDataset"]

from typing import List, Dict, Tuple
import copy

from torch.utils.data import Dataset, DataLoader
import numpy as np
import imageio
from tqdm import tqdm
from ..utils.data import default_collate_fn

class _IdentitiyTransforms():
    """Dummy tranforms"""
    def __call__(self, img: np.ndarray, targets: Dict) -> Tuple:
        return img, targets

class BaseDataset(Dataset):
    def __init__(self, ids: List[str], targets: List[Dict], transforms=None, **kwargs):
        super().__init__()
        assert isinstance(ids, list), "given `ids` must be list"
        assert isinstance(targets, list), "given `targets must be list"
        assert len(ids) == len(targets), "lenght of both lists must be equal"

        self.ids = ids
        self.targets = targets
        self.transforms = _IdentitiyTransforms() if transforms is None else transforms

        # set given kwargs to the dataset
        for key, value in kwargs.items():
            if hasattr(self, key):
                # log warning
                continue
            setattr(self, key, value)

    def __getitem__(self, idx: int) -> Tuple:
        img = self._load_image(self.ids[idx])
        targets = copy.deepcopy(self.targets[idx])

        # apply transforms
        img, targets = self.transforms(img, targets)

        return (img, targets)

    def __len__(self) -> int:
        return len(self.ids)

    @staticmethod
    def _load_image(img_file_path: str):
        """loads rgb image using given file path

        Args:
            img_path (str): image file path to load

        Returns:
            np.ndarray: rgb image as np.ndarray
        """
        img = imageio.imread(img_file_path)
        if not img.flags['C_CONTIGUOUS']:
            # if img is not contiguous than fix it
            img = np.ascontiguousarray(img, dtype=img.dtype)

        if len(img.shape) == 4:
            # found RGBA, converting to => RGB
            img = img[:, :, :3]
        elif len(img.shape) == 2:
            # found GRAYSCALE, converting to => RGB
            img = np.stack([img, img, img], axis=-1)

        return np.array(img, dtype=np.uint8)

    def get_dataloader(self, batch_size: int = 1,
            shuffle: bool = False, num_workers: int = 0,
            collate_fn = default_collate_fn, pin_memory: bool = False, **kwargs
        ):

        return DataLoader(self, batch_size=batch_size, shuffle=shuffle,
            num_workers=num_workers, collate_fn=collate_fn, pin_memory=pin_memory, **kwargs)

    def get_mean_std(self) -> Dict:
        # TODO pydoc
        mean_sum, mean_sq_sum = np.zeros(3), np.zeros(3)
        for img, _ in tqdm(self, total=len(self), desc="calculating mean and std for the dataset"):
            d = img.astype(np.float32) / 255

            mean_sum[0] += np.mean(d[:, :, 0])
            mean_sum[1] += np.mean(d[:, :, 1])
            mean_sum[2] += np.mean(d[:, :, 2])

            mean_sq_sum[0] += np.mean(d[:, :, 0] ** 2)
            mean_sq_sum[1] += np.mean(d[:, :, 1] ** 2)
            mean_sq_sum[2] += np.mean(d[:, :, 2] ** 2)

        mean = mean_sum / len(self)
        std = (mean_sq_sum / len(self) - mean**2)**0.5

        return {"mean": mean.tolist(), "std": std.tolist()}