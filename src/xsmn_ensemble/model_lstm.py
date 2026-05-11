"""
model_lstm.py — Model 5: LSTM/GRU Sequence Model
XSMN-specific: Lookback theo KỲ QUAY, không theo ngày.

Architecture:
  Input:  (batch, seq_len=30, input_dim=100)  ← 30 kỳ, 100 cặp binary
  LSTM:   hidden_dim=64, num_layers=1, dropout=0.2
  FC:     64 → 100
  Sigmoid → probability per pair

Mỗi draw được encode thành vector 100-dim binary:
  v[i] = 1 nếu pair i xuất hiện trong kỳ đó, 0 ngược lại.

LSTM học temporal patterns: cặp nào thường follow cặp nào,
chu kỳ nóng/lạnh, sequential dependencies.

Model được train bởi train_lstm.py và lưu trên Supabase Storage.
Nếu không có model trained → fallback safe (trả lỗi, ensemble vẫn chạy).
"""

import os
import sys
import time
import numpy as np
from datetime import date
from typing import Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.xsmn_ensemble.data_utils import _load_tails_by_draws


# ── Lazy import torch (chỉ import khi cần) ──────────────────────────────────
_torch = None
_nn = None


def _ensure_torch():
    """Lazy-load PyTorch. Trả về (torch, nn) hoặc raise ImportError."""
    global _torch, _nn
    if _torch is None:
        import torch
        import torch.nn as nn
        _torch = torch
        _nn = nn
    return _torch, _nn


# ── LSTM Architecture ────────────────────────────────────────────────────────

class LotteryLSTM:
    """
    Wrapper cho LSTM model dùng trong ensemble prediction.
    
    Architecture:
        LSTM(input=100, hidden=64, layers=1) → Linear(64, 100) → Sigmoid
    
    Input: sequence of binary vectors (mỗi vector = 1 kỳ quay, 100 dims)
    Output: probability for each pair (0-99) in next draw
    """

    def __init__(self, input_dim: int = 100, hidden_dim: int = 64,
                 num_layers: int = 1, dropout: float = 0.2):
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.dropout = dropout
        self.model = None

    def _build_model(self):
        """Build PyTorch LSTM model."""
        torch, nn = _ensure_torch()

        class _LSTMNet(nn.Module):
            def __init__(self, input_dim, hidden_dim, num_layers, dropout):
                super().__init__()
                self.lstm = nn.LSTM(
                    input_size=input_dim,
                    hidden_size=hidden_dim,
                    num_layers=num_layers,
                    batch_first=True,
                    dropout=dropout if num_layers > 1 else 0,
                )
                self.fc = nn.Linear(hidden_dim, input_dim)
                self.sigmoid = nn.Sigmoid()

            def forward(self, x):
                # x: (batch, seq_len, input_dim)
                lstm_out, _ = self.lstm(x)
                # Lấy output cuối cùng trong sequence
                last_output = lstm_out[:, -1, :]  # (batch, hidden_dim)
                logits = self.fc(last_output)      # (batch, input_dim)
                return self.sigmoid(logits)

        self.model = _LSTMNet(self.input_dim, self.hidden_dim,
                              self.num_layers, self.dropout)
        return self.model

    def load(self, model_path: str):
        """Load trained model from .pt file."""
        torch, nn = _ensure_torch()
        if self.model is None:
            self._build_model()
        self.model.load_state_dict(torch.load(model_path, map_location="cpu", weights_only=True))
        self.model.eval()

    def save(self, model_path: str):
        """Save model weights to .pt file."""
        torch, _ = _ensure_torch()
        if self.model is not None:
            torch.save(self.model.state_dict(), model_path)

    def predict_proba(self, sequence: np.ndarray) -> np.ndarray:
        """
        Predict probability for each pair given a sequence of draws.

        Args:
            sequence: np.ndarray shape (seq_len, 100) — binary matrix

        Returns:
            np.ndarray shape (100,) — probability per pair
        """
        torch, _ = _ensure_torch()
        if self.model is None:
            raise RuntimeError("Model chưa được load/train")

        self.model.eval()
        with torch.no_grad():
            x = torch.FloatTensor(sequence).unsqueeze(0)  # (1, seq_len, 100)
            proba = self.model(x).squeeze(0).numpy()       # (100,)
        return proba

    def train_model(self, sequences: np.ndarray, labels: np.ndarray,
                    epochs: int = 80, lr: float = 0.001, verbose: bool = True,
                    val_split: float = 0.2, patience: int = 10, seed: int = 42):
        """
        Train LSTM on historical data with train/val split and early stopping.

        Args:
            sequences: np.ndarray shape (N, seq_len, 100) — training sequences
            labels: np.ndarray shape (N, 100) — binary target vectors
            epochs: max number of training epochs
            lr: learning rate
            verbose: print progress
            val_split: fraction of data for validation (0.2 = 20%)
            patience: early stopping patience (epochs without improvement)
            seed: random seed for reproducibility
        """
        torch, nn = _ensure_torch()
        if self.model is None:
            self._build_model()

        # Reproducibility
        torch.manual_seed(seed)
        np.random.seed(seed)

        # Train/Val split (chronological — không shuffle vì time series)
        n_total = len(sequences)
        n_val = max(int(n_total * val_split), 1) if n_total >= 5 else 0
        n_train = n_total - n_val

        X_all = torch.FloatTensor(sequences)
        y_all = torch.FloatTensor(labels)

        X_train, y_train = X_all[:n_train], y_all[:n_train]
        X_val, y_val = (X_all[n_train:], y_all[n_train:]) if n_val > 0 else (None, None)

        optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)
        criterion = nn.BCELoss()

        # Early stopping state
        best_val_loss = float('inf')
        best_state_dict = None
        epochs_no_improve = 0

        self.model.train()
        for epoch in range(epochs):
            # Training step
            optimizer.zero_grad()
            output = self.model(X_train)
            loss = criterion(output, y_train)
            loss.backward()
            optimizer.step()

            # Validation step
            if X_val is not None:
                self.model.eval()
                with torch.no_grad():
                    val_out = self.model(X_val)
                    val_loss = criterion(val_out, y_val).item()
                self.model.train()

                if val_loss < best_val_loss:
                    best_val_loss = val_loss
                    best_state_dict = {k: v.clone() for k, v in self.model.state_dict().items()}
                    epochs_no_improve = 0
                else:
                    epochs_no_improve += 1

                if verbose and (epoch + 1) % 10 == 0:
                    print(f"  Epoch {epoch+1}/{epochs} — Train: {loss.item():.4f} | Val: {val_loss:.4f}")

                # Early stopping
                if epochs_no_improve >= patience:
                    if verbose:
                        print(f"  ⏹ Early stop at epoch {epoch+1} (patience={patience}, best_val={best_val_loss:.4f})")
                    break
            else:
                if verbose and (epoch + 1) % 10 == 0:
                    print(f"  Epoch {epoch+1}/{epochs} — Loss: {loss.item():.4f}")

        # Restore best weights (if validation was used)
        if best_state_dict is not None:
            self.model.load_state_dict(best_state_dict)

        self.model.eval()


# ── Data Encoding ────────────────────────────────────────────────────────────

def _encode_draws_to_binary(history_df, seq_len: int = 30) -> tuple:
    """
    Encode draw history thành binary matrix cho LSTM.

    Args:
        history_df: DataFrame with 'tail_set' column (frozenset per draw)
        seq_len: số kỳ trong mỗi sequence

    Returns:
        (sequence, None) nếu chỉ predict
        hoặc list of (sequence, label) tuples nếu đủ data cho training
    """
    n = len(history_df)
    if n < seq_len + 1:
        return None, None

    # Encode tất cả kỳ thành binary matrix (n, 100)
    binary_matrix = np.zeros((n, 100), dtype=np.float32)
    for i, tail_set in enumerate(history_df["tail_set"]):
        for pair in tail_set:
            if 0 <= pair <= 99:
                binary_matrix[i, pair] = 1.0

    # Sequence cuối cùng (cho prediction)
    last_seq = binary_matrix[-seq_len:]

    # Training sequences: sliding window
    sequences = []
    labels = []
    for i in range(n - seq_len):
        seq = binary_matrix[i:i + seq_len]
        label = binary_matrix[i + seq_len]
        sequences.append(seq)
        labels.append(label)

    if sequences:
        return last_seq, (np.array(sequences), np.array(labels))
    return last_seq, None


# ── Model Registry Integration ──────────────────────────────────────────────

def _get_lstm_model(db, region: str, province: Optional[str] = None) -> Optional[dict]:
    """Tìm LSTM model active trong model_registry."""
    q = db.supabase.table("model_registry") \
        .select("*") \
        .eq("region", region) \
        .eq("status", "active") \
        .like("model_name", "%lstm%") \
        .order("trained_at", desc=True) \
        .limit(1)

    if province:
        q = q.eq("province", province)
    else:
        q = q.is_("province", "null")

    result = q.execute().data
    if result:
        return result[0]

    # Fallback: model chung (province=NULL)
    q2 = db.supabase.table("model_registry") \
        .select("*") \
        .eq("region", region) \
        .eq("status", "active") \
        .like("model_name", "%lstm%") \
        .is_("province", "null") \
        .order("trained_at", desc=True) \
        .limit(1)
    result2 = q2.execute().data
    return result2[0] if result2 else None


# ── Main Prediction Function ────────────────────────────────────────────────

def predict_lstm(
    db,
    storage=None,
    province: Optional[str] = None,
    target_date: Optional[date] = None,
    n_draws: int = 100,
    seq_len: int = 30,
    top_n: int = 5,
    region: str = "XSMN",
    tmpdir: Optional[str] = None,
) -> Dict:
    """
    Model 5: LSTM/GRU sequence prediction.

    Nếu có model đã train → load và predict.
    Nếu KHÔNG có model → train on-the-fly từ history rồi predict.

    Args:
        db: LotteryDB instance
        storage: LotteryStorage instance (để download model)
        province: slug tỉnh
        target_date: ngày cần dự đoán
        n_draws: số kỳ lookback
        seq_len: chiều dài sequence cho LSTM
        top_n: số cặp top-N output
        region: 'XSMN' hoặc 'XSMB'
        tmpdir: thư mục tạm

    Returns:
        {
            'model_name': 'lstm',
            'province': province,
            'top_pairs': [(pair, probability), ...],
            'n_draws_used': int,
            'model_version': str | None,
            'status': 'success' | 'error',
            'error_message': str | None,
            'execution_time_ms': int,
        }
    """
    start_ms = time.time()

    try:
        # Check torch availability
        try:
            _ensure_torch()
        except ImportError:
            return {
                "model_name": "lstm",
                "province": province,
                "top_pairs": [],
                "n_draws_used": 0,
                "model_version": None,
                "status": "error",
                "error_message": "PyTorch chưa cài đặt (pip install torch)",
                "execution_time_ms": int((time.time() - start_ms) * 1000),
            }

        # Load history
        history = _load_tails_by_draws(db, region, province, n_draws, before_date=target_date)
        n = len(history)

        if n < seq_len + 5:
            return {
                "model_name": "lstm",
                "province": province,
                "top_pairs": [],
                "n_draws_used": n,
                "model_version": None,
                "status": "error",
                "error_message": f"Không đủ lịch sử cho LSTM: {n} kỳ (cần ≥ {seq_len + 5})",
                "execution_time_ms": int((time.time() - start_ms) * 1000),
            }

        # Encode to binary
        last_seq, training_data = _encode_draws_to_binary(history, seq_len)
        if last_seq is None:
            return {
                "model_name": "lstm",
                "province": province,
                "top_pairs": [],
                "n_draws_used": n,
                "model_version": None,
                "status": "error",
                "error_message": "Không thể encode sequence cho LSTM",
                "execution_time_ms": int((time.time() - start_ms) * 1000),
            }

        lstm = LotteryLSTM(input_dim=100, hidden_dim=64, num_layers=1)
        model_version = None
        used_pretrained = False

        # Strategy 1: Thử load model đã train từ registry
        if storage and tmpdir:
            registry = _get_lstm_model(db, region, province)
            if registry and registry.get("file_path"):
                try:
                    file_path = registry["file_path"]
                    local_path = os.path.join(tmpdir, os.path.basename(file_path))
                    if not os.path.exists(local_path):
                        storage.download_model(file_path, local_path)
                    if os.path.exists(local_path):
                        lstm.load(local_path)
                        model_version = registry.get("version")
                        used_pretrained = True
                        print(f"     📦 Loaded pre-trained LSTM: {model_version}")
                except Exception as e:
                    print(f"     ⚠️  Pre-trained LSTM load failed: {e}")

        # Strategy 2: Nếu không có pre-trained → train on-the-fly
        if not used_pretrained:
            if training_data is not None:
                sequences, labels = training_data
                if len(sequences) >= 10:
                    print(f"     🔄 Training LSTM on-the-fly ({len(sequences)} samples, val_split=20%)...")
                    lstm.train_model(sequences, labels, epochs=80, lr=0.002,
                                     val_split=0.2, patience=10, seed=42, verbose=False)
                    model_version = "on_the_fly"
                else:
                    return {
                        "model_name": "lstm",
                        "province": province,
                        "top_pairs": [],
                        "n_draws_used": n,
                        "model_version": None,
                        "status": "error",
                        "error_message": f"Không đủ training samples: {len(sequences)}",
                        "execution_time_ms": int((time.time() - start_ms) * 1000),
                    }
            else:
                return {
                    "model_name": "lstm",
                    "province": province,
                    "top_pairs": [],
                    "n_draws_used": n,
                    "model_version": None,
                    "status": "error",
                    "error_message": "Không có data để train LSTM on-the-fly",
                    "execution_time_ms": int((time.time() - start_ms) * 1000),
                }

        # Predict
        proba = lstm.predict_proba(last_seq)

        # Top N
        top_indices = np.argsort(proba)[-top_n:][::-1]
        top_pairs = [(int(idx), round(float(proba[idx]), 4)) for idx in top_indices]

        return {
            "model_name": "lstm",
            "province": province,
            "top_pairs": top_pairs,
            "n_draws_used": n,
            "model_version": model_version,
            "status": "success",
            "error_message": None,
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }

    except Exception as e:
        return {
            "model_name": "lstm",
            "province": province,
            "top_pairs": [],
            "n_draws_used": 0,
            "model_version": None,
            "status": "error",
            "error_message": str(e),
            "execution_time_ms": int((time.time() - start_ms) * 1000),
        }
