import { useState, useEffect } from 'react';
import axios from 'axios';

interface Guide {
  platform: string;
  steps: string[];
  notes?: string;
}

const LoginGuide = () => {
  const [guide, setGuide] = useState<Guide | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    const fetchGuide = async () => {
      try {
        const response = await axios.get('http://localhost:8000/api/login-guide/facebook', {
          headers: {
            'Authorization': 'Bearer dev-api-key-123',
          }
        });
        setGuide(response.data);
      } catch (err) {
        setError('Failed to load login guide');
      }
    };

    fetchGuide();
  }, []);

  if (error) {
    return (
      <div className="text-red-600 text-center py-4">{error}</div>
    );
  }

  if (!guide) {
    return (
      <div className="text-center py-4">Loading guide...</div>
    );
  }

  return (
    <div className="max-w-md mx-auto bg-white rounded-xl shadow-md overflow-hidden md:max-w-2xl p-6">
      <h2 className="text-2xl font-bold mb-4">{guide.platform} Login Guide</h2>
      <ol className="list-decimal list-inside space-y-2">
        {guide.steps.map((step, index) => (
          <li key={index} className="text-gray-700">{step}</li>
        ))}
      </ol>
      {guide.notes && (
        <div className="mt-4 p-4 bg-blue-50 rounded-md">
          <p className="text-sm text-blue-700">{guide.notes}</p>
        </div>
      )}
    </div>
  );
};

export default LoginGuide;


