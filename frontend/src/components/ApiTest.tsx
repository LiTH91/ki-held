import { useState } from 'react';
import axios from 'axios';

// Add interceptors at top-level of component
axios.interceptors.request.use(request => {
  console.log('[API] Request:', request.method, request.url, request.data);
  return request;
});
axios.interceptors.response.use(response => {
  console.log('[API] Response:', response.status, response.data);
  return response;
});

const ApiTest = () => {
  const [status, setStatus] = useState<string>('');
  const [error, setError] = useState<string>('');

  const testHealthCheck = async () => {
    try {
      setStatus('Loading...');
      setError('');
      console.log('Testing health check endpoint...');
      const response = await axios.get('http://localhost:8000/health', {
        headers: {
          'Accept': 'application/json',
          'Authorization': 'Bearer dev-api-key-123',
        }
      });
      console.log('Health check response:', response.data);
      setStatus(`Backend is healthy! Response: ${JSON.stringify(response.data)}`);
    } catch (err) {
      console.error('Health check error:', err);
      if (axios.isAxiosError(err)) {
        setError(`Failed to connect to backend: ${err.message}`);
        if (err.response) {
          console.error('Error response:', err.response.data);
        }
      } else {
        setError('Failed to connect to backend: Unknown error');
      }
      setStatus('');
    }
  };

  const testLoginGuide = async () => {
    try {
      setStatus('Loading...');
      setError('');
      console.log('Testing login guide endpoint...');
      const response = await axios.get('http://localhost:8000/api/login-guide/facebook', {
        headers: {
          'Accept': 'application/json',
          'Authorization': 'Bearer YOUR_API_KEY_HERE',
        }
      });
      console.log('Login guide response:', response.data);
      setStatus(`Login guide received: ${JSON.stringify(response.data)}`);
    } catch (err) {
      console.error('Login guide error:', err);
      if (axios.isAxiosError(err)) {
        setError(`Failed to fetch login guide: ${err.message}`);
        if (err.response) {
          console.error('Error response:', err.response.data);
        }
      } else {
        setError('Failed to fetch login guide: Unknown error');
      }
      setStatus('');
    }
  };

  return (
    <div className="max-w-2xl mx-auto p-6 bg-white rounded-lg shadow-lg">
      <h2 className="text-2xl font-bold mb-4">API Test Panel</h2>
      
      <div className="space-y-4">
        <div>
          <button
            onClick={testHealthCheck}
            className="bg-blue-500 text-white px-4 py-2 rounded hover:bg-blue-600"
          >
            Test Health Check
          </button>
        </div>

        <div>
          <button
            onClick={testLoginGuide}
            className="bg-green-500 text-white px-4 py-2 rounded hover:bg-green-600"
          >
            Test Login Guide
          </button>
        </div>

        {status && (
          <div className="p-4 bg-green-100 text-green-700 rounded">
            {status}
          </div>
        )}

        {error && (
          <div className="p-4 bg-red-100 text-red-700 rounded">
            {error}
          </div>
        )}
      </div>
    </div>
  );
};

export default ApiTest;