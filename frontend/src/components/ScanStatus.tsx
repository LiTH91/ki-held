import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';
import axios from 'axios';

interface ScanResult {
  is_hate: boolean;
  confidence: number;
  categories: string[];
  explanation: string;
}

interface ScanStatusData {
  status: string;
  messages: string[];
  result?: ScanResult;
}

const ScanStatus = () => {
  const location = useLocation();
  const [statusData, setStatusData] = useState<ScanStatusData | null>(null);
  const [error, setError] = useState('');
  const scanId = location.state?.scanId;
  const [username, setUsername] = useState('');

  useEffect(() => {
    if (!scanId) {
      setError('No scan ID provided');
      return;
    }

    const interval = setInterval(async () => {
      try {
        console.log(`[DEBUG] Polling for scan ID: ${scanId}`);
        const response = await axios.get<ScanStatusData>(`http://localhost:8000/api/scan/${scanId}`);
        console.log('[DEBUG] Received data from backend:', response.data);
        setStatusData(response.data);
        
        if (response.data.status === 'failed') {
          clearInterval(interval);
          const lastMessage = response.data.messages[response.data.messages.length - 1];
          setError(lastMessage || 'Scan failed');
        } else if (response.data.status === 'completed') {
          clearInterval(interval);
        }
      } catch (err) {
        setError('Failed to get scan status');
        clearInterval(interval);
      }
    }, 2000); // Poll every 2 seconds

    return () => clearInterval(interval);
  }, [scanId]);

  const handleExport = () => {
    // TODO: Implement PDF export functionality
    console.log('Exporting PDF for scan ID:', scanId);
  };

  const handleAssignUser = async () => {
    try {
      await axios.post(`http://localhost:8000/api/scan/${scanId}/assign-user`, { username });
      // Optionally, show a success message
    } catch (err) {
      setError('Failed to assign user. Please try again.');
    }
  };

  console.log('[DEBUG] Rendering with statusData:', statusData);

  if (error) {
    return <div className="text-red-500 text-center p-4">{error}</div>;
  }

  if (!statusData) {
    return <div className="text-center py-4">Loading status...</div>;
  }

  return (
    <div className="max-w-md mx-auto bg-white rounded-xl shadow-md overflow-hidden md:max-w-2xl p-6">
      <h2 className="text-2xl font-bold mb-4">Scan Status</h2>
      <div className="space-y-4">
        <div>
          <span className="font-medium">Scan ID:</span>
          <span className="ml-2 text-gray-600">{scanId}</span>
        </div>
        <div>
          <span className="font-medium">Status:</span>
          <span
            className={`ml-2 font-semibold ${
              statusData.status === 'completed' ? 'text-green-600' : 
              statusData.status === 'failed' ? 'text-red-600' : 'text-blue-600'
            }`}
          >
            {statusData.status}
          </span>
        </div>
        
        <div>
          <p className="font-medium">Progress:</p>
          <ul className="list-disc list-inside bg-gray-50 p-2 rounded">
            {statusData.messages.map((msg, index) => (
              <li key={index} className="text-gray-700">{msg}</li>
            ))}
          </ul>
        </div>

        {statusData.status === 'completed' && (
          <div className="mt-6">
            <h3 className="text-lg leading-6 font-medium text-gray-900">Scan Complete</h3>
            {statusData.result ? (
              <div className="mt-2 text-sm text-gray-600">
                <p className={`font-semibold ${statusData.result.is_hate ? 'text-red-600' : 'text-green-600'}`}>
                  {statusData.result.is_hate ? 'Hate Speech Detected' : 'No Hate Speech Detected'}
                </p>
                <p><strong>Confidence:</strong> {statusData.result.confidence ? (statusData.result.confidence * 100).toFixed(2) + '%' : 'N/A'}</p>
                <p><strong>Categories:</strong> {statusData.result.categories ? statusData.result.categories.join(', ') : 'None'}</p>
                <p><strong>Explanation:</strong> {statusData.result.explanation || 'No explanation provided.'}</p>
              </div>
            ) : (
              <p className="mt-2 text-sm text-green-600 font-semibold">
                No hate speech was detected during the scan.
              </p>
            )}
            <div className="mt-4">
              <label htmlFor="username" className="block text-sm font-medium text-gray-700">
                Assign to Username
              </label>
              <input
                type="text"
                id="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="mt-1 block w-full rounded-md border-gray-300 shadow-sm"
              />
              <button onClick={handleAssignUser} className="mt-2 w-full flex justify-center py-2 px-4 border">
                Assign User and Save Evidence
              </button>
            </div>
          </div>
        )}
        {statusData.status === 'completed' && (
          <button
            onClick={handleExport}
            disabled // Temporarily disable until backend is implemented
            className="mt-4 w-full flex justify-center py-2 px-4 border border-transparent rounded-md shadow-sm text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 disabled:opacity-50"
          >
            Export PDF Evidence (Coming Soon)
          </button>
        )}
      </div>
    </div>
  );
};

export default ScanStatus;


