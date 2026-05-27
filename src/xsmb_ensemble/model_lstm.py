"""
model_lstm.py — Model E: Bi-LSTM + Attention (XSMB v4)

Enhanced LSTM/GRU cho XSMB:
  - Sequence length: 30 → 60 (XSMB có đủ data)
  - Bidirectional LSTM: capture forward + backward patterns
  - Self-Attention: weight kỳ quan trọng trong sequence
  - Feature-enriched input: 100-dim binary → 200-dim
    (100 binary + 100 gap_since_last per pair)
  - GRU variant: train cả LSTM và GRU, chọn tốt hơn

Model load từ model_registry (pre-trained) hoặc train on-the-fly.
"""

import os
import sys
import time
import numpy as np
from datetime import date
from typing import Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.xsmb_ensemble.data_utils import _load_tails_by_draws

# ── Lazy import torch ────────────────────────────────────────────────────────
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


# ── Enhanced LSTM Architecture (v4) ──────────────────────────────────────────

class XSMBLSTMv4:
    """
    Bi-LSTM + Attention model cho XSMB.

    Architecture:
        Input: (batch, seq_len=60, input_dim=200)
               ← 100 binary + 100 gap features
        BiLSTM: hidden=64, bidirectional=True → output 128
        Attention: self-attention over sequence positions
        FC: 128 → 100
        Sigmoid → probability per pair
    """

    def __init__(
        self,
        input_dim: int = 200,
        hidden_dim: int = 64,
        num_layers: int = 1,
        dropout: float = 0.2,
        use_attention: bool = True,
    ):
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.dropout = dropout
        self.use_attention = use_attention
        self.model = None

    def _build_model(self):
        """Build PyTorch BiLSTM + Attention model."""
        torch, nn = _ensure_torch()
        use_attn = self.use_attention

        class _BiLSTMAttention(nn.Module):
            def __init__(self, input_dim, hidden_dim, num_layers, dropout, use_attention):
                super().__init__()
                self.use_attention = use_attention
                self.lstm = nn.LSTM(
                    input_size=input_dim,
                    hidden_size=hidden_dim,
                    num_layers=num_layers,
                    batch_first=True,
                    bidirectional=True,
                    dropout=dropout if num_layers > 1 else 0,
                )
                # Bidirectional → output dim = hidden_dim * 2
                lstm_output_dim = hidden_dim * 2

                if use_attention:
                    # Self-attention: score = tanh(W × h) → softmax
                    self.attn_W = nn.Linear(lstm_output_dim, lstm_output_dim, bias=False)
                    self.attn_v = nn.Linear(lstm_output_dim, 1, bias=False)

                self.fc = nn.Linear(lstm_output_dim, 100)
                self.sigmoid = nn.Sigmoid()

            def forward(self, x):
                # x: (batch, seq_len, input_dim)
                lstm_out, _ = self.lstm(x)  # (batch, seq_len, hidden*2)

                if self.use_attention:
                    # Attention scores
                    energy = torch.tanh(self.attn_W(lstm_out))  # (batch, seq, hidden*2)
                    scores = self.attn_v(energy).squeeze(-1)     # (batch, seq)
                    attn_weights = torch.softmax(scores, dim=-1)  # (batch, seq)

                    # Weighted sum
                    context = torch.bmm(
                        attn_weights.unsqueeze(1), lstm_out
                    ).squeeze(1)  # (batch, hidden*2)
                else:
                    # Simple: lấy output cuối
                    context = lstm_out[:, -1, :]  # (batch, hidden*2)

                logits = self.fc(context)    # (batch, 100)
                return self.sigmoid(logits)

        self.model = _BiLSTMAttention(
            self.input_dim, self.hidden_dim, self.num_layers,
            self.dropout, self.use_attention
        )
        return self.model

    def load(self, model_path: str):
        """Load trained model from .pt file."""
        torch, nn = _ensure_torch()
        if self.model is None:
            self._build_model()
        state_dict = torch.load(model_path, map_location="cpu", weights_only=True)
        self.model.load_state_dict(state_dict)
        self.model.eval()

    def save(self, model_path: str):
        """Save model weights to .pt file."""
        torch, _ = _ensure_torch()
        if self.model is not None:
            torch.save(self.model.state_dict(), model_path)

    def predict_proba(self, sequence: np.ndarray) -> np.ndarray:
        """
        Predict probability for each pair given a sequence.

        Args:
            sequence: np.ndarray shape (seq_len, input_dim)

        Returns:
            np.ndarray shape (100,) — probability per pair
        """
        torch, _ = _ensure_torch()
        if self.model is None:
            raise RuntimeError("Model chưa được load/train")

        self.model.eval()
        with torch.no_grad():
            x = torch.FloatTensor(sequence).unsqueeze(0)  # (1, seq_len, input_dim)
            proba = self.model(x).squeeze(0).numpy()       # (100,)
        return proba

    def train_model(
        self,
        sequences: np.ndarray,
        labels: np.ndarray,
        epochs: int = 80,
        lr: float = 0.001,
        val_split: float = 0.2,
        patience: int = 10,
        seed: int = 42,
        verbose: bool = True,
    ):
        """Train BiLSTM+Attention with early stopping."""
        torch, nn = _ensure_torch()
        if self.model is None:
            self._build_model()

        torch.manual_seed(seed)
        np.random.seed(seed)

        n_total = len(sequences)
        n_val = max(int(n_total * val_split), 1) if n_total >= 5 else 0
        n_train = n_total - n_val

        X_all = torch.FloatTensor(sequences)
        y_all = torch.FloatTensor(labels)

        X_train, y_train = X_all[:n_train], y_all[:n_train]
        X_val, y_val = (X_all[n_train:], y_all[n_train:]) if n_val > 0 else (None, None)

        optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)
        criterion = nn.BCELoss()

        best_val_loss = float('inf')
        best_state_dict = None
        epochs_no_improve = 0

        self.model.train()
        for epoch in range(epochs):
            optimizer.zero_grad()
            output = self.model(X_train)
            loss = criterion(output, y_train)
            loss.backward()
            optimizer.step()

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

                if epochs_no_improve >= patience:
                    if verbose:
                        print(f"  ⏹ Early stop epoch {epoch+1} (patience={patience}, best={best_val_loss:.4f})")
                    break
            else:
                if verbose and (epoch + 1) % 10 == 0:
                    print(f"  Epoch {epoch+1}/{epochs} — Loss: {loss.item():.4f}")

        if best_state_dict is not None:
            self.model.load_state_dict(best_state_dict)
        self.model.eval()


# ── Data Encoding (v4: enriched input) ──────────────────────────────────────

def _encode_draws_enriched(
    history_df,
    seq_len: int = 60,
) -> tuple:
    """
    Encode draw history thành enriched matrix cho LSTM v4.

    Input per timestep = 200-dim:
      - [0:100]:   binary appearance (pair 0-99)
      - [100:200]: gap_since_last per pair (normalized)

    Args:
        history_df: DataFrame with 'tail_set' column
        seq_len: sequence length

    Returns:
        (last_sequence, training_data) or (None, None)
    """
    n = len(history_df)
    if n < seq_len + 1:
        return None, None

    # Binary matrix (n, 100)
    binary = np.zeros((n, 100), dtype=np.float32)
    for i, tail_set in enumerate(history_df["tail_set"]):
        for pair in tail_set:
            if 0 <= pair <= 99:
                binary[i, pair] = 1.0

    # Gap matrix (n, 100): normalized gap_since_last at each timestep
    gap_matrix = np.zeros((n, 100), dtype=np.float32)
    for pair in range(100):
        last_seen = -1
        for i in range(n):
            if binary[i, pair] > 0.5:
                last_seen = i
            gap = (i - last_seen) if last_seen >= 0 else (i + 1)
            # Normalize gap to [0, 1]: gap > 30 → capped at 1.0
            gap_matrix[i, pair] = min(gap / 30.0, 1.0)

    # Concatenate: (n, 200)
    enriched = np.concatenate([binary, gap_matrix], axis=1)

    # Last sequence for prediction
    last_seq = enriched[-seq_len:]

    # Training sequences: sliding window
    sequences = []
    labels = []
    for i in range(n - seq_len):
        seq = enriched[i:i + seq_len]
        label = binary[i + seq_len]  # only binary target
        sequences.append(seq)
        labels.append(label)

    if sequences:
        return last_seq, (np.array(sequences), np.array(labels))
    return last_seq, None


# ── Model Registry ──────────────────────────────────────────────────────────

def _get_lstm_model(db, region: str = "XSMB", province: Optional[str] = None) -> Optional[dict]:
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

    # Fallback: province=NULL
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


# ── Main Prediction ─────────────────────────────────────────────────────────

def predict_lstm(
    db,
    storage=None,
    province: Optional[str] = None,
    target_date: Optional[date] = None,
    n_draws: int = 180,
    seq_len: int = 60,
    top_n: int = 5,
    region: str = "XSMB",
    tmpdir: Optional[str] = None,
) -> Dict:
    """
    Model E: Bi-LSTM + Attention cho XSMB.

    Nếu có pre-trained model → load và predict.
    Nếu KHÔNG → train on-the-fly (BiLSTM, input_dim=200, seq_len=60).

    Args:
        db: LotteryDB instance
        storage: LotteryStorage instance
        province: None cho XSMB
        target_date: ngày predict
        n_draws: lookback
        seq_len: LSTM sequence length (default 60 for XSMB)
        top_n: top-N output
        region: 'XSMB'
        tmpdir: temp dir for model cache

    Returns:
        Dict with model_name, top_pairs, status, etc.
    """
    start_ms = time.time()

    try:
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

        # Encode with enriched features (200-dim)
        last_seq, training_data = _encode_draws_enriched(history, seq_len)
        if last_seq is None:
            return {
                "model_name": "lstm",
                "province": province,
                "top_pairs": [],
                "n_draws_used": n,
                "model_version": None,
                "status": "error",
                "error_message": "Không thể encode sequence",
                "execution_time_ms": int((time.time() - start_ms) * 1000),
            }

        lstm = XSMBLSTMv4(input_dim=200, hidden_dim=64, num_layers=1, use_attention=True)
        model_version = None
        used_pretrained = False

        # Strategy 1: Load pre-trained model
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
                        print(f"     📦 Loaded pre-trained BiLSTM v4: {model_version}")
                except Exception as e:
                    print(f"     ⚠️  Pre-trained LSTM load failed: {e}")

        # Strategy 2: Train on-the-fly
        if not used_pretrained:
            if training_data is not None:
                sequences, labels = training_data
                if len(sequences) >= 15:
                    print(f"     🔄 Training BiLSTM v4 on-the-fly ({len(sequences)} samples)...")
                    lstm.train_model(
                        sequences, labels, epochs=80, lr=0.002,
                        val_split=0.2, patience=10, seed=42, verbose=False
                    )
                    model_version = "on_the_fly_v4"
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
