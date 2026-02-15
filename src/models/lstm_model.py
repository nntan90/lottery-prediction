"""
LSTM Model for Lottery Prediction
"""

import numpy as np
import tensorflow as tf
from tensorflow.keras.models import Sequential, load_model
from tensorflow.keras.layers import LSTM, Dense, Dropout
from sklearn.preprocessing import MinMaxScaler
import pandas as pd
from typing import List, Dict, Tuple, Optional
import os

class LotteryLSTM:
    def __init__(self, sequence_length: int = 60):
        self.sequence_length = sequence_length
        self.model = None
        self.scaler = MinMaxScaler(feature_range=(0, 1))
        
    def prepare_data(self, data: List[Dict], prize_column: str = 'special_prize') -> Tuple[np.ndarray, np.ndarray]:
        """
        Prepare data for LSTM training.
        Extracts numerical values from the specified prize column.
        """
        df = pd.DataFrame(data)
        
        # Sort by date
        if 'draw_date' in df.columns:
            df['draw_date'] = pd.to_datetime(df['draw_date'])
            df = df.sort_values('draw_date')
            
        # Extract series
        series = df[prize_column].astype(str).apply(lambda x: float(x) if x.isdigit() else 0).values
        series = series.reshape(-1, 1)
        
        # Normalize
        scaled_data = self.scaler.fit_transform(series)
        
        X, y = [], []
        for i in range(self.sequence_length, len(scaled_data)):
            X.append(scaled_data[i-self.sequence_length:i, 0])
            y.append(scaled_data[i, 0])
            
        return np.array(X), np.array(y)

    def build_model(self, input_shape: Tuple[int, int]):
        """Build LSTM architecture"""
        model = Sequential()
        model.add(LSTM(units=50, return_sequences=True, input_shape=input_shape))
        model.add(Dropout(0.2))
        model.add(LSTM(units=50, return_sequences=False))
        model.add(Dropout(0.2))
        model.add(Dense(units=1))
        
        model.compile(optimizer='adam', loss='mean_squared_error')
        self.model = model
        return model

    def train(self, X: np.ndarray, y: np.ndarray, epochs: int = 50, batch_size: int = 32):
        """Train the model"""
        # Reshape X to (samples, time steps, features)
        X = np.reshape(X, (X.shape[0], X.shape[1], 1))
        
        if self.model is None:
            self.build_model((X.shape[1], 1))
            
        history = self.model.fit(X, y, epochs=epochs, batch_size=batch_size, validation_split=0.1, verbose=1)
        return history

    def predict_next(self, recent_data: np.ndarray) -> float:
        """
        Predict the next value.
        recent_data: valid numpy array of last `sequence_length` values
        """
        if self.model is None:
            raise ValueError("Model not trained or loaded!")
            
        # Reshape and Normalize if needed (assuming recent_data is already scaled for this simple example, 
        # or we scaler.transform it here but we need the scaler fit from training)
        # For simplicity in this v1, assume input is raw and we use self.scaler
        
        # Actually, best to pass raw list and let us scale
        input_data = recent_data.reshape(-1, 1)
        scaled_input = self.scaler.transform(input_data)
        
        X_test = np.reshape(scaled_input, (1, self.sequence_length, 1))
        predicted_scaled = self.model.predict(X_test)
        
        predicted_value = self.scaler.inverse_transform(predicted_scaled)
        return float(predicted_value[0][0])

    def save(self, filepath: str):
        """Save model to .h5 file"""
        if self.model:
            self.model.save(filepath)
            print(f"✅ Model saved to {filepath}")

    def load(self, filepath: str):
        """Load model from .h5 file"""
        if os.path.exists(filepath):
            self.model = load_model(filepath)
            print(f"✅ Model loaded from {filepath}")
        else:
            print(f"❌ Model file not found: {filepath}")
